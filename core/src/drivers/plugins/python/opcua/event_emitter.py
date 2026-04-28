"""
OPC UA Event Emitter for non-AlarmCondition events.

The companion piece to alarm_builder.py / alarm_manager.py. While those
two cover the full Part 9 Alarm & Condition machinery (state, ack,
confirm, retain), real plants emit a wider variety of OPC UA events:

  - AuditWriteUpdateEventType    (Part 5 §A.4) — operator wrote a value
  - AuditUpdateMethodEventType   (Part 5 §A.5) — operator called a Method
                                  (Acknowledge, Confirm, custom triggers)
  - SystemStatusChangeEventType  (Part 5 §6.4.32) — server lifecycle
  - BaseEventType                (Part 5 §6.4.2) — generic informational
                                  event (lead rotation, backwash, refill,
                                  any narrative HMI signal)

asyncua's `Server.get_event_generator()` is type-agnostic — it accepts
any event type's NodeId, instantiates an event payload object with the
spec'd fields, and trigger()s it on the chosen emitting node. The
existing alarm path uses this for AlarmCondition subtypes; this module
sets up the same machinery for the four base types above.

All emitters root at the Server node. Asyncua's MonitoredItemService
dispatches events on exact emitting_node match, and these event types
are universally subscribed at the Server node — there's no spec-mandated
"per-source" subscription pattern for them like there is for Conditions.
So a single Server-rooted EventGenerator per type is sufficient (no
dual-emission like AlarmRuntime needs).

Wiring (see L2 hooks in server.py / synchronization.py / alarm_manager.py):
  - `synchronization.SynchronizationManager` calls `emit_audit_write()`
    when its sync_opcua_to_runtime detects a changed readwrite node.
  - `server.OpcuaServerManager` calls `emit_system_status()` on
    server start and shutdown.
  - `alarm_manager.AlarmManager` calls `emit_audit_method()` from its
    Acknowledge/Confirm handlers.

Lifecycle: build() must run AFTER `server.init()` (which materialises
the standard event types in the address space) and BEFORE
`server.start()` so EventGenerators exist when the first client connects.
"""

import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from asyncua import Server, ua

_current_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_current_dir)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

try:
    from .opcua_logging import log_debug, log_error, log_info, log_warn
except ImportError:
    from opcua_logging import log_debug, log_error, log_info, log_warn


# Spec NumericIds (OPC UA Part 5 / Part 6).
_AUDIT_WRITE_UPDATE_EVENT_TYPE = ua.ObjectIds.AuditWriteUpdateEventType        # 2100
_AUDIT_UPDATE_METHOD_EVENT_TYPE = ua.ObjectIds.AuditUpdateMethodEventType      # 2127
_SYSTEM_STATUS_CHANGE_EVENT_TYPE = ua.ObjectIds.SystemStatusChangeEventType    # 11446
_BASE_EVENT_TYPE = ua.ObjectIds.BaseEventType                                  # 2041

# Identifies events emitted by this runtime in audit logs / SIEM
# downstream. Read by clients via ServerId and by us as the SourceName
# fallback when no specific source is supplied.
_RUNTIME_SOURCE_NAME = "OpenPLC Runtime"


class EventEmitter:
    """Owns one shared EventGenerator per non-alarm event type and
    exposes typed emit_* helpers. The four EventGenerators are
    Server-rooted; clients subscribe on the Server node and receive
    every type our runtime fires.

    All emit_* methods are best-effort: failures log and continue. We
    never want a stray event-payload bug to kill the PLC scan loop.
    """

    def __init__(self, server: Server):
        self.server = server

        # EventGenerators — populated by build(). None until then.
        self._gen_audit_write: Optional[object] = None
        self._gen_audit_method: Optional[object] = None
        self._gen_system_status: Optional[object] = None
        self._gen_base: Optional[object] = None

        # Cached server NodeId for SourceNode field. Filled in build().
        self._server_node_id: Optional[ua.NodeId] = None

    async def build(self) -> bool:
        """Instantiate the four EventGenerators on the Server node.

        Must run after `server.init()` (so the asyncua type machinery
        knows about the event types) and before `server.start()` (so
        the generators exist when the first subscription is created).

        Returns True if at least the BaseEventType generator was
        created — that's the minimum we need. Per-type failures are
        logged but don't abort.
        """
        if not self.server:
            log_warn("EventEmitter.build() called with no Server reference")
            return False

        try:
            self._server_node_id = self.server.nodes.server.nodeid
        except Exception as e:
            log_warn(f"EventEmitter: failed to resolve Server NodeId: {e}")
            self._server_node_id = None

        ok_count = 0
        for attr, type_id, label in (
            ("_gen_audit_write",   _AUDIT_WRITE_UPDATE_EVENT_TYPE,   "AuditWriteUpdateEventType"),
            ("_gen_audit_method",  _AUDIT_UPDATE_METHOD_EVENT_TYPE,  "AuditUpdateMethodEventType"),
            ("_gen_system_status", _SYSTEM_STATUS_CHANGE_EVENT_TYPE, "SystemStatusChangeEventType"),
            ("_gen_base",          _BASE_EVENT_TYPE,                 "BaseEventType"),
        ):
            try:
                gen = await self.server.get_event_generator(
                    type_id,
                    self.server.nodes.server,
                )
                # Seed steady-state identity fields. Per-emit overrides
                # rewrite the variable bits (Message, Severity, payload).
                if self._server_node_id is not None:
                    gen.event.SourceNode = self._server_node_id
                gen.event.SourceName = _RUNTIME_SOURCE_NAME
                setattr(self, attr, gen)
                ok_count += 1
                log_debug(f"EventEmitter: built {label} generator")
            except Exception as e:
                log_warn(f"EventEmitter: failed to build {label}: {e}")

        log_info(f"EventEmitter ready: {ok_count}/4 event types available")
        return ok_count > 0

    # ------------------------------------------------------------------
    # AuditWriteUpdateEventType — operator wrote a value to an OPC UA
    # variable. Spec fields (Part 5 §A.4): SourceNode (the *written*
    # node), AttributeId (13 = Value), IndexRange, OldValue, NewValue,
    # ClientUserId (the writing user, or "unknown" when polling layer
    # can't identify them), ActionTimeStamp.
    # ------------------------------------------------------------------
    async def emit_audit_write(
        self,
        node_id: ua.NodeId,
        node_path: str,
        old_value: Any,
        new_value: Any,
        client_user_id: str = "unknown",
    ) -> None:
        if self._gen_audit_write is None:
            return
        try:
            ev = self._gen_audit_write.event
            now = datetime.now(timezone.utc)

            # Identity: point SourceNode at the *written* variable, not
            # the Server node. SourceName carries the dotted path so
            # downstream consumers don't need to resolve the NodeId.
            ev.SourceNode = node_id
            ev.SourceName = node_path
            ev.Severity = 100  # informational — audit, not alarm
            ev.Message = ua.LocalizedText(f"Write to {node_path}")

            # Type-specific fields. asyncua exposes them as plain
            # attributes on ev because instantiate_optional=True
            # populated them during type-machinery setup.
            try:
                setattr(ev, "ActionTimeStamp", now)
                setattr(ev, "Status", True)  # write succeeded — caller fires post-write
                setattr(ev, "ServerId", _RUNTIME_SOURCE_NAME)
                setattr(ev, "ClientUserId", client_user_id)
                setattr(ev, "AttributeId", ua.AttributeIds.Value)
                setattr(ev, "IndexRange", "")
                setattr(ev, "OldValue", _to_variant(old_value))
                setattr(ev, "NewValue", _to_variant(new_value))
            except Exception as e:
                log_debug(f"emit_audit_write: optional field set failed: {e}")

            await self._gen_audit_write.trigger(time_attr=now)
        except Exception as e:
            log_error(f"emit_audit_write({node_path}) failed: {e}")

    # ------------------------------------------------------------------
    # AuditUpdateMethodEventType — operator called a server Method.
    # Used today for Acknowledge / Confirm on AlarmConditions; can be
    # extended to any Method bound by future config. Spec fields
    # (Part 5 §A.5): MethodId, InputArguments, ClientUserId.
    # ------------------------------------------------------------------
    async def emit_audit_method(
        self,
        method_node_id: Optional[ua.NodeId],
        method_name: str,
        target_node_id: ua.NodeId,
        target_name: str,
        client_user_id: str = "unknown",
        input_args: Optional[list] = None,
        status_ok: bool = True,
    ) -> None:
        if self._gen_audit_method is None:
            return
        try:
            ev = self._gen_audit_method.event
            now = datetime.now(timezone.utc)
            ev.SourceNode = target_node_id
            ev.SourceName = target_name
            ev.Severity = 100
            ev.Message = ua.LocalizedText(f"{method_name} called on {target_name}")
            try:
                setattr(ev, "ActionTimeStamp", now)
                setattr(ev, "Status", bool(status_ok))
                setattr(ev, "ServerId", _RUNTIME_SOURCE_NAME)
                setattr(ev, "ClientUserId", client_user_id)
                if method_node_id is not None:
                    setattr(ev, "MethodId", method_node_id)
                if input_args is not None:
                    setattr(ev, "InputArguments", input_args)
            except Exception as e:
                log_debug(f"emit_audit_method: optional field set failed: {e}")
            await self._gen_audit_method.trigger(time_attr=now)
        except Exception as e:
            log_error(f"emit_audit_method({method_name}) failed: {e}")

    # ------------------------------------------------------------------
    # SystemStatusChangeEventType — server lifecycle (start, shutdown,
    # PLC reload). Spec field: SystemState (string).
    # ------------------------------------------------------------------
    async def emit_system_status(
        self,
        message: str,
        system_state: str = "Running",
        severity: int = 200,
    ) -> None:
        if self._gen_system_status is None:
            return
        try:
            ev = self._gen_system_status.event
            now = datetime.now(timezone.utc)
            if self._server_node_id is not None:
                ev.SourceNode = self._server_node_id
            ev.SourceName = _RUNTIME_SOURCE_NAME
            ev.Severity = severity
            ev.Message = ua.LocalizedText(message)
            try:
                setattr(ev, "SystemState", system_state)
            except Exception as e:
                log_debug(f"emit_system_status: optional field set failed: {e}")
            await self._gen_system_status.trigger(time_attr=now)
        except Exception as e:
            log_error(f"emit_system_status({message!r}) failed: {e}")

    # ------------------------------------------------------------------
    # BaseEventType — generic informational. Use for narrative HMI
    # signals that don't map to a standardised type.
    # ------------------------------------------------------------------
    async def emit_info(
        self,
        source_name: str,
        message: str,
        severity: int = 100,
        source_node_id: Optional[ua.NodeId] = None,
    ) -> None:
        if self._gen_base is None:
            return
        try:
            ev = self._gen_base.event
            now = datetime.now(timezone.utc)
            ev.SourceNode = source_node_id if source_node_id is not None else (
                self._server_node_id or ua.NodeId(0, 0)
            )
            ev.SourceName = source_name
            ev.Severity = severity
            ev.Message = ua.LocalizedText(message)
            await self._gen_base.trigger(time_attr=now)
        except Exception as e:
            log_error(f"emit_info({source_name!r}) failed: {e}")


def _to_variant(value: Any) -> ua.Variant:
    """Best-effort conversion of a Python value to a ua.Variant. Used
    for AuditWriteUpdate's OldValue/NewValue fields which are typed
    BaseDataType (i.e. any). Falls back to string-encoding when we
    can't infer a closer type — still informative, never crashes."""
    if value is None:
        return ua.Variant(None, ua.VariantType.Null)
    if isinstance(value, bool):
        return ua.Variant(value, ua.VariantType.Boolean)
    if isinstance(value, int):
        return ua.Variant(value, ua.VariantType.Int64)
    if isinstance(value, float):
        return ua.Variant(value, ua.VariantType.Double)
    if isinstance(value, str):
        return ua.Variant(value, ua.VariantType.String)
    return ua.Variant(str(value), ua.VariantType.String)
