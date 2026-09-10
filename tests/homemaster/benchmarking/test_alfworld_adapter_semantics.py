"""Stateful contract regressions; real runtime gates remain separate."""
from types import SimpleNamespace

import pytest

from homemaster.benchmarking.alfworld.env_adapter import _execute_heat, _execute_cool, _execute_clean


class StatefulThor:
    def __init__(self, appliance, *, opened=False, toggled=False, ignore_actions=False):
        self.ignore_actions = ignore_actions
        self.calls = []
        self.heated_objects = set()
        self.cooled_objects = set()
        self.cleaned_objects = set()
        self.apple = dict(objectId="Apple|1", objectType="Apple", pickupable=True,
                          isPickedUp=True, parentReceptacles=[], position=dict(x=0,y=0,z=0))
        self.tool = dict(objectId=appliance+"|1", objectType=appliance, receptacle=True,
                         openable=appliance != "SinkBasin", isOpen=opened, isToggled=toggled,
                         receptacleObjectIds=[], position=dict(x=0,y=0,z=0))
        self.faucet = dict(objectId="Faucet|1", objectType="Faucet", isToggled=toggled,
                           position=dict(x=0,y=0,z=0))
        self.last_event = SimpleNamespace(metadata=dict(objects=[self.apple,self.tool,self.faucet],
                    inventoryObjects=[dict(objectId="Apple|1")], lastActionSuccess=True))

    def step(self, action):
        self.calls.append(action)
        m = self.last_event.metadata
        m.update(lastActionSuccess=True, errorMessage="")
        obj = next(o for o in m["objects"] if o["objectId"] == action["objectId"])
        kind = action["action"]
        field, desired = {"OpenObject": ("isOpen",True), "CloseObject": ("isOpen",False),
                          "ToggleObjectOn": ("isToggled",True), "ToggleObjectOff": ("isToggled",False)}.get(kind,(None,None))
        if field and (obj[field] == desired or (kind == "OpenObject" and obj["isToggled"])):
            m.update(lastActionSuccess=False, errorMessage="Nothing happens / Target must be OFF to open")
        elif not self.ignore_actions:
            if field:
                obj[field] = desired
            if kind == "PutObject":
                self.apple.update(isPickedUp=False, parentReceptacles=[self.tool["objectId"]])
                self.tool["receptacleObjectIds"] = ["Apple|1"]
                m["inventoryObjects"] = []
            elif kind == "PickupObject":
                self.apple.update(isPickedUp=True, parentReceptacles=[])
                self.tool["receptacleObjectIds"] = []
                m["inventoryObjects"] = [dict(objectId="Apple|1")]
            if kind == "ToggleObjectOn":
                ids = set(self.tool["receptacleObjectIds"])
                if "Microwave" in action["objectId"]:
                    self.heated_objects.update(ids)
                if "Faucet" in action["objectId"]:
                    self.cleaned_objects.update(ids)
            if kind == "CloseObject" and "Fridge" in action["objectId"]:
                self.cooled_objects.update(self.tool["receptacleObjectIds"])
        return self.last_event


@pytest.mark.parametrize("fn,appliance,opened,toggled,attribute", [
    (_execute_heat,"Microwave",True,False,"heated_objects"),
    (_execute_heat,"Microwave",False,True,"heated_objects"),
    (_execute_cool,"Fridge",True,False,"cooled_objects"),
    (_execute_clean,"SinkBasin",False,True,"cleaned_objects"),
])
def test_macro_handles_existing_appliance_state(fn, appliance, opened, toggled, attribute):
    env = StatefulThor(appliance, opened=opened, toggled=toggled)
    result = fn(env, {})
    assert result.success, result.feedback
    assert "Apple|1" in getattr(env, attribute)
    assert env.last_event.metadata["inventoryObjects"] == [dict(objectId="Apple|1")]
    assert len(result.backend_actions) == len(env.calls)


def test_macro_rejects_success_receipt_without_world_change():
    env = StatefulThor("Microwave", ignore_actions=True)
    result = _execute_heat(env, {})
    assert not result.success
    assert len(env.calls) == 1, "stop at first contradictory receipt"

@pytest.mark.parametrize("action,expected", [("heat", "microwave"), ("cool", "fridge"), ("clean", "sinkbasin")])
def test_macro_navigation_targets_appliance(action, expected):
    from homemaster.benchmarking.alfworld.tools import _navigation_target_for_action
    assert _navigation_target_for_action({"action": action, "object": "apple"}) == expected
    assert _navigation_target_for_action({"action": action, "object": "apple", "tool_receptacle": expected + " 1"}) == expected + " 1"
