"""
OPC UA Alarm & Condition runtime.

Runs once per scan cycle from synchronization.run(). For each registered
alarm, it reads the driving BOOL inputs from the PLC, compares them
against the previous-cycle snapshot, and on any transition mutates the
EventGenerator's event payload and calls trigger() to broadcast a
ConditionType event to subscribers.

The PLC is the source of truth for whether an alarm condition holds —
we don't re-evaluate analog-vs-threshold here. The PLC's IEC 61131
program drives the BOOL `*_alarm` tags; this module just translates
their edges into spec-faithful A&C events.

Retain semantics (OPC UA Part 9 §5.5.2):
  Retain=True while the Condition is "interesting" — i.e. either still
  active or active-but-unacknowledged. Once both Inactive AND
  Acknowledged, Retain=False so ConditionRefresh stops returning it.
"""

import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from asyncua import ua

_current_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_current_dir)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

try:
    from .opcua_logging import log_debug, log_error, log_info, log_warn
    from .alarm_builder import AlarmRuntime
except ImportError:
    from opcua_logging import log_debug, log_error, log_info, log_warn
    from alarm_builder import AlarmRuntime

from asyncua import Server
from shared import SafeBufferAccess


class _AlarmState:
    """Per-alarm cached state used for edge detection. The event_objects
    payload itself is the canonical "current state" served to clients;
    this is just the cycle-over-cycle delta detector."""
    __slots__ = ("input_bools", "active", "acked", "confirmed", "last_event_id")

    def __init__(self):
        # input_bools: semantic role -> last-seen Boolean from PLC
        self.input_bools: Dict[str, bool] = {}
        self.active: bool = False
        # Initial steady state matches what alarm_builder seeded into
        # the event payload: enabled, inactive, acknowledged, confirmed.
        self.acked: bool = True
        self.confirmed: bool = True
        # Most recent EventId we emitted for this Condition. Acknowledge
        # methods compare against this to validate the EventId argument.
        self.last_event_id: Optional[bytes] = None


class AlarmManager:
    """Drives Condition state-change events from PLC BOOL transitions.
    One instance per OPC UA plugin. Owns the alarm registry produced by
    AlarmBuilder."""

    def __init__(
        self,
        buffer_accessor: SafeBufferAccess,
        runtime: List[AlarmRuntime],
        server: Optional[Server] = None,
    ):
        self.buffer_accessor = buffer_accessor
        self.runtime = runtime
        self.server = server
        # Parallel array (same order as self.runtime) of cached state.
        self.state: List[_AlarmState] = [_AlarmState() for _ in runtime]
        # Lookup helpers used by the Acknowledge/Confirm method handlers.
        self.by_condition_nodeid: Dict[ua.NodeId, int] = {}
        for i, ar in enumerate(self.runtime):
            self.by_condition_nodeid[ar.condition_node.nodeid] = i

    async def initialize(self) -> None:
        """One-shot priming: read the current PLC BOOL values without
        firing events, then register the Acknowledge/Confirm method
        callbacks per Condition. Avoids a spurious "everything just
        transitioned" burst on the very first cycle."""
        if not self.runtime:
            return
        all_indices = self._all_input_indices()
        if all_indices:
            values = self._read_indices(all_indices)
            for i, ar in enumerate(self.runtime):
                for role, idx in ar.input_indices.items():
                    v = values.get(idx)
                    if isinstance(v, bool):
                        self.state[i].input_bools[role] = v
                    elif isinstance(v, (int, float)):
                        self.state[i].input_bools[role] = bool(v)
            # Seed `active` from the primed inputs so the very first real
            # cycle only fires for genuine changes.
            for i, ar in enumerate(self.runtime):
                self.state[i].active = self._compute_active(ar, self.state[i].input_bools)

        await self._register_method_callbacks()

        log_debug(
            f"AlarmManager primed {len(self.runtime)} alarms "
            f"({sum(1 for s in self.state if s.active)} initially active)"
        )

    async def _register_method_callbacks(self) -> None:
        """Wire Acknowledge and Confirm methods on each Condition. The
        instantiated method nodes live as children of the Condition
        Object — same browse names as the OPC UA standard, so we look
        them up via "0:Acknowledge" / "0:Confirm". Failures per-condition
        are logged but don't abort registration of the rest."""
        if self.server is None:
            log_warn("AlarmManager has no Server reference; ack/confirm methods unbound")
            return

        registered_ack = 0
        registered_conf = 0
        for i, ar in enumerate(self.runtime):
            try:
                ack_node = await ar.condition_node.get_child("0:Acknowledge")
                self.server.link_method(ack_node, self._make_ack_handler(i))
                registered_ack += 1
            except Exception as e:
                log_warn(
                    f"alarm {ar.alarm.node_id}: Acknowledge method "
                    f"unavailable ({e})"
                )
            try:
                conf_node = await ar.condition_node.get_child("0:Confirm")
                self.server.link_method(conf_node, self._make_confirm_handler(i))
                registered_conf += 1
            except Exception as e:
                log_warn(
                    f"alarm {ar.alarm.node_id}: Confirm method "
                    f"unavailable ({e})"
                )

        log_debug(
            f"Registered {registered_ack} Acknowledge and {registered_conf} "
            f"Confirm handlers across {len(self.runtime)} Conditions"
        )

    def _make_ack_handler(self, idx: int):
        """Build a closure that captures the alarm registry index. The
        resulting coroutine matches asyncua's method-callback signature:
        (parent_objectid, *input_args)."""
        async def handler(parent, event_id=None, comment=None):
            try:
                return await self._handle_acknowledge(idx, event_id, comment)
            except Exception as e:
                log_error(f"Acknowledge handler raised for alarm {idx}: {e}")
                return ua.StatusCode(ua.StatusCodes.BadInternalError)
        return handler

    def _make_confirm_handler(self, idx: int):
        async def handler(parent, event_id=None, comment=None):
            try:
                return await self._handle_confirm(idx, event_id, comment)
            except Exception as e:
                log_error(f"Confirm handler raised for alarm {idx}: {e}")
                return ua.StatusCode(ua.StatusCodes.BadInternalError)
        return handler

    async def _handle_acknowledge(self, idx: int, event_id, comment):
        """Acknowledge transitions AckedState/Id from False to True.
        Per OPC UA Part 9, calling Acknowledge on an already-acked
        Condition should return BadConditionBranchAlreadyAcked. We
        accept stale EventIds (don't enforce match) so UAExpert's
        button works even if the visible event is older than the
        current one — matches Prosys/Siemens behaviour."""
        ar = self.runtime[idx]
        st = self.state[idx]
        if st.acked:
            log_debug(f"alarm {ar.alarm.node_id}: Acknowledge ignored (already acked)")
            return ua.StatusCode(ua.StatusCodes.BadConditionBranchAlreadyAcked)
        st.acked = True
        log_info(f"alarm {ar.alarm.node_id}: Acknowledged")
        await self._fire_event(ar, st, st.input_bools)
        return ua.StatusCode()

    async def _handle_confirm(self, idx: int, event_id, comment):
        """Confirm follows Acknowledge in the standard A&C state
        machine — transitions ConfirmedState/Id from False to True.
        Some clients only use Acknowledge, others enforce both phases."""
        ar = self.runtime[idx]
        st = self.state[idx]
        if st.confirmed:
            log_debug(f"alarm {ar.alarm.node_id}: Confirm ignored (already confirmed)")
            return ua.StatusCode(ua.StatusCodes.BadConditionBranchAlreadyConfirmed)
        st.confirmed = True
        log_info(f"alarm {ar.alarm.node_id}: Confirmed")
        await self._fire_event(ar, st, st.input_bools)
        return ua.StatusCode()

    async def process_cycle(self) -> None:
        """Read all alarm input BOOLs, detect transitions, fire events.
        Cheap when nothing changes (one batch read + per-alarm dict
        compare); only triggers the event-broadcast path on actual
        state changes."""
        if not self.runtime:
            return
        try:
            all_indices = self._all_input_indices()
            if not all_indices:
                return
            values = self._read_indices(all_indices)

            for i, ar in enumerate(self.runtime):
                try:
                    await self._process_alarm(i, ar, values)
                except Exception as e:
                    log_error(
                        f"alarm {ar.alarm.node_id} cycle processing failed: {e}"
                    )
        except Exception as e:
            log_error(f"AlarmManager.process_cycle failed: {e}")
            traceback.print_exc()

    async def _process_alarm(
        self,
        idx: int,
        ar: AlarmRuntime,
        values: Dict[int, object],
    ) -> None:
        st = self.state[idx]
        new_inputs: Dict[str, bool] = {}
        for role, var_index in ar.input_indices.items():
            v = values.get(var_index)
            if isinstance(v, bool):
                new_inputs[role] = v
            elif isinstance(v, (int, float)):
                new_inputs[role] = bool(v)
            else:
                # No fresh read this cycle (e.g. PLC just unloaded);
                # carry the previous value forward to avoid spurious
                # transitions.
                new_inputs[role] = st.input_bools.get(role, False)

        new_active = self._compute_active(ar, new_inputs)
        inputs_changed = new_inputs != st.input_bools
        active_changed = new_active != st.active
        if not inputs_changed and not active_changed:
            return

        # Edge detected — update cached state, mutate event payload,
        # fire. We fire on *any* input edge (not just active-state
        # change) so individual HighState / LowState transitions are
        # visible to subscribers — that matches Siemens/Beckhoff
        # behaviour for NonExclusiveLevel.
        st.input_bools = new_inputs
        prev_active = st.active
        st.active = new_active

        # Active rising edge resets the ack/confirm chain — operator
        # has to acknowledge the new excursion (OPC UA Part 9 §5.7.3).
        if new_active and not prev_active:
            st.acked = False
            st.confirmed = False

        await self._fire_event(ar, st, new_inputs)

    def _compute_active(
        self,
        ar: AlarmRuntime,
        inputs: Dict[str, bool],
    ) -> bool:
        """Translate the alarm's BOOL inputs into ActiveState. The PLC
        decides — we just OR them together for level alarms."""
        atype = ar.alarm.alarm_type
        if atype == "OffNormal":
            return inputs.get("input", False)
        # NonExclusiveLevel and ExclusiveLevel: active if either side
        # asserts. For ExclusiveLevel only one side is configured, so
        # the missing role defaults to False and the OR collapses.
        return inputs.get("high", False) or inputs.get("low", False)

    async def _fire_event(
        self,
        ar: AlarmRuntime,
        st: _AlarmState,
        inputs: Dict[str, bool],
    ) -> None:
        """Mutate the EventGenerator's event payload to reflect current
        state, then trigger() to broadcast. asyncua handles EventId
        regeneration and Time/ReceiveTime stamping inside trigger()."""
        ev = ar.event_generator.event
        alarm = ar.alarm
        now = datetime.now(timezone.utc)

        active = st.active
        # Severity: report at config level while active, drop to 1
        # (informational) when returning to normal — standard OPC UA
        # convention so historians can plot severity vs time.
        ev.Severity = alarm.severity if active else 1
        setattr(ev, "LastSeverity", alarm.severity)
        ev.Message = ua.LocalizedText(
            alarm.message_active if active else alarm.message_inactive
        )

        # ActiveState. We do NOT set ActiveState/TransitionTime here:
        # asyncua's Variant constructor crashes (`'NodeId' has no
        # attribute __name__`) when wrapping a non-None value whose
        # data_type was registered as a NodeId rather than a
        # VariantType — and TransitionTime fields are exactly that.
        # The event's own Time field is set by trigger() and clients
        # use that as the transition timestamp.
        setattr(ev, "ActiveState/Id", active)
        setattr(ev, "ActiveState",
                ua.LocalizedText("Active" if active else "Inactive"))

        # AckedState / ConfirmedState — reset on rising-edge active,
        # otherwise reflect cached state (which Acknowledge/Confirm
        # method handlers will mutate from outside this module).
        setattr(ev, "AckedState/Id", st.acked)
        setattr(ev, "AckedState",
                ua.LocalizedText("Acknowledged" if st.acked else "Unacknowledged"))
        setattr(ev, "ConfirmedState/Id", st.confirmed)
        setattr(ev, "ConfirmedState",
                ua.LocalizedText("Confirmed" if st.confirmed else "Unconfirmed"))

        # Per-side state for NonExclusiveLevel (no TransitionTime —
        # see comment above).
        if alarm.alarm_type == "NonExclusiveLevel":
            high = inputs.get("high", False)
            low = inputs.get("low", False)
            setattr(ev, "HighState/Id", high)
            setattr(ev, "HighState",
                    ua.LocalizedText("High" if high else "Inactive"))
            setattr(ev, "LowState/Id", low)
            setattr(ev, "LowState",
                    ua.LocalizedText("Low" if low else "Inactive"))

        # Retain controls visibility in ConditionRefresh: keep True while
        # the operator still cares (active, or active-and-since-cleared
        # but not yet acknowledged). Goes False once the alarm is both
        # back to normal and acknowledged.
        retain = active or (not st.acked)
        setattr(ev, "Retain", retain)

        # Quality stays Good — we have no separate path for sensor-bad
        # in this PLC contract.
        setattr(ev, "Quality", ua.StatusCode(ua.StatusCodes.Good))

        try:
            await ar.event_generator.trigger(time_attr=now)
            # asyncua's trigger() generates a fresh EventId per call;
            # capture it for Acknowledge/Confirm correlation.
            try:
                eid = ev.EventId
                if hasattr(eid, "Value"):
                    eid = eid.Value
                if isinstance(eid, (bytes, bytearray)):
                    st.last_event_id = bytes(eid)
            except Exception:
                pass
        except Exception as e:
            log_error(
                f"Failed to fire event for {alarm.node_id}: {e}"
            )
            return

        # Mirror the event state onto the Condition's address-space
        # property nodes so clients that browse-read (UAExpert's
        # Attributes pane, polling SCADA, the discovery walker) see
        # the same current state that subscribers just received.
        await self._mirror_state_to_properties(ar, st, inputs, active, retain)

        log_debug(
            f"alarm {alarm.node_id}: active={active} acked={st.acked} "
            f"retain={retain} inputs={inputs}"
        )

    async def _mirror_state_to_properties(
        self,
        ar: AlarmRuntime,
        st: _AlarmState,
        inputs: Dict[str, bool],
        active: bool,
        retain: bool,
    ) -> None:
        """Write current state into the Condition's child Property
        nodes. The event payload is what subscribers receive; these
        properties are what browse-readers see. Both must agree or
        clients get inconsistent views.

        Uses server.write_attribute_value (the server-internal path)
        rather than node.write_value so the writes bypass the PreWrite
        permission callback — Condition properties aren't declared in
        node_permissions and would otherwise fail-closed."""
        cn = ar.condition_node
        alarm = ar.alarm
        server = self.server

        async def write(browse: str, value, vtype):
            try:
                child = await cn.get_child(f"0:{browse}")
            except Exception:
                return  # Property doesn't exist on this alarm subtype
            try:
                dv = ua.DataValue(ua.Variant(value, vtype))
                if server is not None:
                    await server.write_attribute_value(child.nodeid, dv)
                else:
                    await child.write_value(ua.Variant(value, vtype))
            except Exception as e:
                log_warn(f"property mirror failed for {alarm.node_id}.{browse}: {e}")

        await write("Severity", alarm.severity if active else 1, ua.VariantType.UInt16)
        await write("LastSeverity", alarm.severity, ua.VariantType.UInt16)
        await write("Message",
                    ua.LocalizedText(alarm.message_active if active else alarm.message_inactive),
                    ua.VariantType.LocalizedText)
        await write("Retain", retain, ua.VariantType.Boolean)
        await write("ActiveState/Id", active, ua.VariantType.Boolean)
        await write("ActiveState",
                    ua.LocalizedText("Active" if active else "Inactive"),
                    ua.VariantType.LocalizedText)
        await write("AckedState/Id", st.acked, ua.VariantType.Boolean)
        await write("AckedState",
                    ua.LocalizedText("Acknowledged" if st.acked else "Unacknowledged"),
                    ua.VariantType.LocalizedText)
        await write("ConfirmedState/Id", st.confirmed, ua.VariantType.Boolean)
        await write("ConfirmedState",
                    ua.LocalizedText("Confirmed" if st.confirmed else "Unconfirmed"),
                    ua.VariantType.LocalizedText)
        if alarm.alarm_type == "NonExclusiveLevel":
            high = inputs.get("high", False)
            low = inputs.get("low", False)
            await write("HighState/Id", high, ua.VariantType.Boolean)
            await write("HighState",
                        ua.LocalizedText("High" if high else "Inactive"),
                        ua.VariantType.LocalizedText)
            await write("LowState/Id", low, ua.VariantType.Boolean)
            await write("LowState",
                        ua.LocalizedText("Low" if low else "Inactive"),
                        ua.VariantType.LocalizedText)

    def _all_input_indices(self) -> List[int]:
        seen: List[int] = []
        seen_set = set()
        for ar in self.runtime:
            for idx in ar.input_indices.values():
                if idx not in seen_set:
                    seen.append(idx)
                    seen_set.add(idx)
        return seen

    def _read_indices(self, indices: List[int]) -> Dict[int, object]:
        """Batch-read PLC values for the given indices. Returns a dict
        keyed by index; missing keys mean the read failed for that var
        (e.g. PLC unloaded), and the caller carries previous state
        forward to avoid spurious transitions."""
        out: Dict[int, object] = {}
        try:
            results, msg = self.buffer_accessor.get_var_values_batch(indices)
            if "Exception" in msg or "Error" in msg:
                log_warn(f"AlarmManager batch read failed: {msg}")
                return out
            for i, (value, vmsg) in enumerate(results):
                if vmsg == "Success" and value is not None:
                    out[indices[i]] = value
        except Exception as e:
            log_error(f"AlarmManager batch read raised: {e}")
        return out
