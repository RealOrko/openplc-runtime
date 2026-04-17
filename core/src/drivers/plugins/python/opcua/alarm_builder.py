"""
OPC UA Alarm & Condition builder.

Instantiates Condition Object nodes (NonExclusiveLevelAlarmType,
ExclusiveLevelAlarmType, OffNormalAlarmType) into the address space
alongside the regular variables. Each Condition is created via
asyncua's `add_object(objecttype=..., instantiate_optional=True)` so
the standard A&C property tree (EnabledState, ActiveState, AckedState,
HighLimit, LowLimit, etc.) is materialised by the asyncua type
machinery, not hand-rolled here.

Sets EventNotifier=1 on the Server node, every Area folder that
contains a Condition, and every Condition node itself — that's the
correct OPC UA convention for advertising event sources to discovery
walks.

Runtime ownership: this module only builds the address-space surface
and the per-alarm EventGenerator. Transition detection and trigger()
calls live in alarm_manager.py.
"""

import os
import sys
import traceback
from typing import Dict, List, NamedTuple, Optional

from asyncua import Server, ua
from asyncua.common.node import Node

_current_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_current_dir)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

try:
    from .opcua_logging import log_debug, log_error, log_info, log_warn
    from .address_space import AddressSpaceBuilder
except ImportError:
    from opcua_logging import log_debug, log_error, log_info, log_warn
    from address_space import AddressSpaceBuilder

from shared.plugin_config_decode.opcua_config_model import (
    AlarmCondition,
    OpcuaConfig,
)


# Map alarm_type (config string) -> asyncua TypeDefinition NodeId.
# These are the canonical OPC UA NumericIds defined in Part 9.
_ALARM_TYPE_NODEIDS = {
    "NonExclusiveLevel": ua.ObjectIds.NonExclusiveLevelAlarmType,  # 10060
    "ExclusiveLevel":    ua.ObjectIds.ExclusiveLevelAlarmType,     # 9482
    "OffNormal":         ua.ObjectIds.OffNormalAlarmType,          # 10637
}


class AlarmRuntime(NamedTuple):
    """One row of the alarm registry, handed to alarm_manager.py.

    Carries TWO EventGenerators per Condition:
      - event_generator_server: emitting_node = Server. Used by clients
        that subscribe events on the Server node (asyncua's
        `subscribe_alarms_and_conditions(server)` and UAExpert default).
      - event_generator_condition: emitting_node = Condition itself.
        Used by clients that follow the OPC UA spec and subscribe per
        Condition (open62541-based clients, sindarin-pkg-net-opcua,
        most industrial SCADA tooling).

    asyncua's MonitoredItemService keys event subscriptions by exact
    NodeId match — there's no spec-style HasNotifier bubbling — so a
    single emitting_node would only reach one of the two subscription
    styles. Dual emission satisfies both.
    """
    alarm: AlarmCondition
    condition_node: Node
    event_generator_server: object     # asyncua EventGenerator
    event_generator_condition: object  # asyncua EventGenerator
    # Map of semantic input role -> PLC debug-variable index. Keys are
    # one of: "input" (OffNormal), "high" / "low" (level alarms).
    input_indices: Dict[str, int]
    # Source variable node_id (dotted) for level alarms, else None.
    source_node_id: Optional[str]


class AlarmBuilder:
    """Constructs Condition Object nodes and EventGenerators from
    AlarmCondition declarations, given an already-built variable
    address space.
    """

    def __init__(
        self,
        server: Server,
        namespace_idx: int,
        config: OpcuaConfig,
        address_space_builder: AddressSpaceBuilder,
    ):
        self.server = server
        self.namespace_idx = namespace_idx
        self.config = config
        self.addr = address_space_builder

        # Output registry consumed by alarm_manager
        self.runtime: List[AlarmRuntime] = []

        # Reverse lookup populated lazily: dotted node_id -> (Node, var_index)
        self._var_lookup: Dict[str, tuple] = {}

    def _build_var_lookup(self) -> None:
        """Build a dotted-node_id -> (Node, debug_var_index) lookup from
        the AddressSpaceBuilder's outputs. Used to resolve InputNode and
        source variable references on each Condition."""
        for var_index, var_node in self.addr.variable_nodes.items():
            try:
                dotted = self.addr.nodeid_to_variable.get(var_node.node.nodeid)
                if dotted:
                    self._var_lookup[dotted] = (var_node.node, var_index)
            except Exception:
                continue

    async def build(self) -> bool:
        alarms = self.config.address_space.alarms
        if not alarms:
            log_debug("No alarms declared; skipping AlarmBuilder")
            return True

        self._build_var_lookup()

        objects = self.server.get_objects_node()
        created = 0

        for alarm in alarms:
            try:
                # Resolve parent folder (reusing the variable-side helper
                # so we share the folder cache and never create a duplicate
                # Plant/Intake/Source_Reservoir hierarchy).
                parent = await self.addr._resolve_parent(objects, alarm.node_id)

                condition_node = await self._create_condition(parent, alarm)
                # Build TWO EventGenerators per Condition (see AlarmRuntime
                # docstring for why). The Server-rooted one fires to
                # asyncua-style subscribers; the Condition-rooted one
                # fires to spec-style per-Condition subscribers.
                ev_gen_server = await self.server.get_event_generator(
                    _ALARM_TYPE_NODEIDS[alarm.alarm_type],
                    self.server.nodes.server,
                )
                ev_gen_condition = await self.server.get_event_generator(
                    _ALARM_TYPE_NODEIDS[alarm.alarm_type],
                    condition_node,
                )

                # Seed both event payloads with the same steady-state
                # values. The alarm starts Enabled, Inactive,
                # Acknowledged, Confirmed, Retain=False. alarm_manager
                # rewrites both events identically per transition.
                self._seed_event_payload(ev_gen_server.event, alarm, condition_node)
                self._seed_event_payload(ev_gen_condition.event, alarm, condition_node)

                # Mirror the seeded values onto the address-space property
                # children so a client that browses the Condition (vs.
                # subscribing to events) sees the same state.
                await self._populate_condition_properties(condition_node, alarm)

                # Promote the parent folder chain to event sources so
                # discovery walks find them. We climb from the Condition's
                # immediate parent up to (but not past) the Objects root.
                await self._enable_event_notifier_on_path(parent)

                input_indices = self._resolve_input_indices(alarm)
                self.runtime.append(AlarmRuntime(
                    alarm=alarm,
                    condition_node=condition_node,
                    event_generator_server=ev_gen_server,
                    event_generator_condition=ev_gen_condition,
                    input_indices=input_indices,
                    source_node_id=alarm.source_node_id,
                ))
                created += 1
                log_debug(
                    f"Created Condition {alarm.node_id} "
                    f"({alarm.alarm_type}, severity={alarm.severity}, "
                    f"inputs={list(input_indices.keys())})"
                )
            except Exception as e:
                log_error(f"Failed to create alarm {alarm.node_id}: {e}")
                traceback.print_exc()

        log_info(
            f"AlarmBuilder created {created}/{len(alarms)} Conditions; "
            f"runtime registry has {len(self.runtime)} entries"
        )
        return created > 0

    async def _create_condition(self, parent: Node, alarm: AlarmCondition) -> Node:
        """Create the Condition Object node under `parent` with the right
        TypeDefinition. asyncua's `add_object` with objecttype +
        instantiate_optional=True walks the type's HasComponent /
        HasProperty references and instantiates the full standard
        property tree (EnabledState, ActiveState, AckedState,
        HighLimit/LowLimit for limit alarms, etc.) automatically."""
        type_id = ua.NodeId(_ALARM_TYPE_NODEIDS[alarm.alarm_type])
        condition_node = await parent.add_object(
            self.namespace_idx,
            alarm.browse_name,
            objecttype=type_id,
            instantiate_optional=True,
        )

        await condition_node.write_attribute(
            ua.AttributeIds.DisplayName,
            ua.DataValue(ua.Variant(
                ua.LocalizedText(alarm.display_name),
                ua.VariantType.LocalizedText,
            )),
        )
        await condition_node.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.Variant(
                ua.LocalizedText(alarm.description),
                ua.VariantType.LocalizedText,
            )),
        )

        # Set EventNotifier=1 on the Condition itself so a client can
        # subscribe directly to events from this node (as opposed to
        # subscribing on the Server). The EventGenerator.init() sets
        # this too as a side effect, but doing it explicitly keeps
        # discovery walks happy even before the first event is fired.
        await condition_node.write_attribute(
            ua.AttributeIds.EventNotifier,
            ua.DataValue(ua.Variant(1, ua.VariantType.Byte)),
        )
        return condition_node

    async def _enable_event_notifier_on_path(self, leaf_parent: Node) -> None:
        """Walk from `leaf_parent` (the Condition's direct parent folder)
        up to but not including the Objects root, setting EventNotifier=1
        on each Area folder. Folders that contain only Variables stay at
        0; only those that contain Conditions get advertised."""
        objects = self.server.get_objects_node()
        node = leaf_parent
        while node and node.nodeid != objects.nodeid:
            try:
                await node.write_attribute(
                    ua.AttributeIds.EventNotifier,
                    ua.DataValue(ua.Variant(1, ua.VariantType.Byte)),
                )
                # Hop to parent via the inverse Organizes / HasComponent ref
                refs = await node.get_references(
                    refs=ua.ObjectIds.HierarchicalReferences,
                    direction=ua.BrowseDirection.Inverse,
                    includesubtypes=True,
                )
                if not refs:
                    break
                node = self.server.get_node(refs[0].NodeId)
            except Exception as e:
                log_warn(f"Failed setting EventNotifier on parent folder: {e}")
                break

    def _seed_event_payload(
        self,
        event: object,
        alarm: AlarmCondition,
        condition_node: Node,
    ) -> None:
        """Set the steady-state attribute values on the event_objects
        instance held by the EventGenerator. setattr is used directly
        because asyncua's BaseEvent.add_property() stores attributes
        with slash-separated names (e.g. "EnabledState/Id") that aren't
        legal Python attribute syntax."""
        # Identity
        event.SourceNode = condition_node.nodeid
        event.SourceName = alarm.browse_name
        setattr(event, "ConditionName", alarm.browse_name)
        setattr(event, "BranchId", ua.NodeId(0, 0))  # 0 = current state, not a history branch
        setattr(event, "Retain", False)
        event.Severity = alarm.severity
        setattr(event, "LastSeverity", alarm.severity)
        event.Message = ua.LocalizedText(alarm.message_inactive)

        # State machine: Enabled, Inactive, Acknowledged, Confirmed
        setattr(event, "EnabledState", ua.LocalizedText("Enabled"))
        setattr(event, "EnabledState/Id", True)
        setattr(event, "EnabledState/TrueState", ua.LocalizedText("Enabled"))
        setattr(event, "EnabledState/FalseState", ua.LocalizedText("Disabled"))
        setattr(event, "ActiveState", ua.LocalizedText("Inactive"))
        setattr(event, "ActiveState/Id", False)
        setattr(event, "ActiveState/TrueState", ua.LocalizedText("Active"))
        setattr(event, "ActiveState/FalseState", ua.LocalizedText("Inactive"))
        setattr(event, "AckedState", ua.LocalizedText("Acknowledged"))
        setattr(event, "AckedState/Id", True)
        setattr(event, "AckedState/TrueState", ua.LocalizedText("Acknowledged"))
        setattr(event, "AckedState/FalseState", ua.LocalizedText("Unacknowledged"))
        setattr(event, "ConfirmedState", ua.LocalizedText("Confirmed"))
        setattr(event, "ConfirmedState/Id", True)
        setattr(event, "ConfirmedState/TrueState", ua.LocalizedText("Confirmed"))
        setattr(event, "ConfirmedState/FalseState", ua.LocalizedText("Unconfirmed"))
        setattr(event, "Quality", ua.StatusCode(ua.StatusCodes.Good))

        # InputNode points at the analog source for level alarms or the
        # BOOL fault flag for OffNormal. Either way, it tells clients
        # which Variable's state drives this Condition.
        input_node_id = self._resolve_input_node_id(alarm)
        if input_node_id and input_node_id in self._var_lookup:
            setattr(event, "InputNode", self._var_lookup[input_node_id][0].nodeid)

        # Type-specific fields
        if alarm.alarm_type == "NonExclusiveLevel":
            setattr(event, "HighLimit", float(alarm.high_limit))
            setattr(event, "LowLimit", float(alarm.low_limit))
            setattr(event, "HighState", ua.LocalizedText("Inactive"))
            setattr(event, "HighState/Id", False)
            setattr(event, "HighState/TrueState", ua.LocalizedText("High"))
            setattr(event, "HighState/FalseState", ua.LocalizedText("Inactive"))
            setattr(event, "LowState", ua.LocalizedText("Inactive"))
            setattr(event, "LowState/Id", False)
            setattr(event, "LowState/TrueState", ua.LocalizedText("Low"))
            setattr(event, "LowState/FalseState", ua.LocalizedText("Inactive"))
        elif alarm.alarm_type == "ExclusiveLevel":
            if alarm.high_limit is not None:
                setattr(event, "HighLimit", float(alarm.high_limit))
            if alarm.low_limit is not None:
                setattr(event, "LowLimit", float(alarm.low_limit))
        elif alarm.alarm_type == "OffNormal":
            # NormalState references the Variable whose value defines
            # "normal". For our PLC-driven fault flags, the input BOOL
            # itself is the indicator: False=normal, True=fault. Point
            # NormalState at it so clients know what to read.
            if input_node_id and input_node_id in self._var_lookup:
                setattr(event, "NormalState", self._var_lookup[input_node_id][0].nodeid)

    async def _populate_condition_properties(
        self,
        condition_node: Node,
        alarm: AlarmCondition,
    ) -> None:
        """Mirror the seeded event values onto the Condition's address-
        space property children. Clients that browse the Condition (vs.
        subscribing to events) read these — UAExpert's "Attributes" pane,
        a discovery walker, etc. Without this they'd see uninitialized
        nulls.

        Errors are logged and swallowed: not every property exists on
        every alarm type (HighLimit on OffNormal, NormalState on level
        alarms), and we don't want a missing property to abort the
        whole build.
        """
        async def write(browse: str, value, vtype):
            try:
                child = await condition_node.get_child(f"0:{browse}")
                await child.write_value(ua.Variant(value, vtype))
            except Exception:
                pass  # property not present on this alarm type — fine

        await write("Severity", alarm.severity, ua.VariantType.UInt16)
        await write("Message", ua.LocalizedText(alarm.message_inactive), ua.VariantType.LocalizedText)
        await write("EnabledState/Id", True, ua.VariantType.Boolean)
        await write("ActiveState/Id", False, ua.VariantType.Boolean)
        await write("AckedState/Id", True, ua.VariantType.Boolean)
        await write("ConfirmedState/Id", True, ua.VariantType.Boolean)
        await write("Retain", False, ua.VariantType.Boolean)

        if alarm.alarm_type in ("NonExclusiveLevel", "ExclusiveLevel"):
            if alarm.high_limit is not None:
                await write("HighLimit", float(alarm.high_limit), ua.VariantType.Double)
            if alarm.low_limit is not None:
                await write("LowLimit", float(alarm.low_limit), ua.VariantType.Double)

        if alarm.alarm_type == "NonExclusiveLevel":
            await write("HighState/Id", False, ua.VariantType.Boolean)
            await write("LowState/Id", False, ua.VariantType.Boolean)

        # InputNode reference — for level alarms the analog Variable;
        # for OffNormal the BOOL fault flag. Browsable as a property.
        input_node_id = self._resolve_input_node_id(alarm)
        if input_node_id and input_node_id in self._var_lookup:
            input_nodeid = self._var_lookup[input_node_id][0].nodeid
            await write("InputNode", input_nodeid, ua.VariantType.NodeId)

    def _resolve_input_node_id(self, alarm: AlarmCondition) -> Optional[str]:
        """Return the dotted node_id of the variable that semantically
        represents this alarm's monitored input. For level alarms the
        source analog variable; for OffNormal the fault BOOL itself."""
        if alarm.alarm_type == "OffNormal":
            return alarm.input_node_id
        return alarm.source_node_id

    def _resolve_input_indices(self, alarm: AlarmCondition) -> Dict[str, int]:
        """Resolve the BOOL inputs that drive Condition state into PLC
        debug-variable indices. alarm_manager reads these every cycle to
        detect transitions."""
        out: Dict[str, int] = {}
        if alarm.alarm_type == "OffNormal":
            ref = self._var_lookup.get(alarm.input_node_id)
            if ref:
                out["input"] = ref[1]
        else:
            if alarm.high_input_node_id:
                ref = self._var_lookup.get(alarm.high_input_node_id)
                if ref:
                    out["high"] = ref[1]
            if alarm.low_input_node_id:
                ref = self._var_lookup.get(alarm.low_input_node_id)
                if ref:
                    out["low"] = ref[1]
        return out
