from __future__ import annotations
from typing import Any, Optional
from copy import deepcopy
from .models import Canon, GameState, Event, Primitive

class IntegrityError(Exception):
    """Ошибка целостности состояния."""
    pass

def build_initial(canon: Canon) -> GameState:
    """Инициализирует состояние из канона."""
    item_placement = {}
    npc_locations = {}
    
    for loc in canon.locations.values():
        item_placement[loc.id] = {}
        for entity_id in loc.entities:
            if entity_id in canon.npcs:
                npc_locations[entity_id] = loc.id
            elif entity_id in canon.items:
                item_placement[loc.id][entity_id] = item_placement[loc.id].get(entity_id, 0) + 1
    
    return GameState(
        turn=0,
        location=canon.initial_state.location,
        inventory=dict(canon.initial_state.inventory),
        stats=dict(canon.initial_state.stats),
        flags=dict(canon.initial_state.flags),
        item_placement=item_placement,
        npc_locations=npc_locations
    )

def apply(state: GameState, primitives: list[Primitive]) -> GameState:
    """Атомарно применяет список примитивов к состоянию. При ошибке состояние не меняется."""
    new_inventory = dict(state.inventory)
    new_stats = dict(state.stats)
    new_flags = dict(state.flags)
    new_item_placement = {loc_id: dict(items) for loc_id, items in state.item_placement.items()}
    new_location = state.location
    new_turn = state.turn
    
    for prim in primitives:
        if prim.type == "set_flag":
            if prim.flag not in new_flags:
                raise IntegrityError(f"Unknown flag: {prim.flag}")
            new_flags[prim.flag] = prim.value
            
        elif prim.type == "add_to_inventory":
            if prim.item not in new_inventory:
                new_inventory[prim.item] = 0
            new_inventory[prim.item] += prim.count
            
        elif prim.type == "remove_from_inventory":
            if prim.item not in new_inventory:
                raise IntegrityError(f"Item not in inventory: {prim.item}")
            new_inventory[prim.item] -= prim.count
            if new_inventory[prim.item] < 0:
                raise IntegrityError(f"Cannot remove more than available: {prim.item}")
            if new_inventory[prim.item] == 0:
                del new_inventory[prim.item]
                
        elif prim.type == "add_to_location":
            if prim.to not in new_item_placement:
                new_item_placement[prim.to] = {}
            if prim.item not in new_item_placement[prim.to]:
                new_item_placement[prim.to][prim.item] = 0
            new_item_placement[prim.to][prim.item] += prim.count
            
        elif prim.type == "remove_from_location":
            if prim.to not in new_item_placement:
                raise IntegrityError(f"Location not found: {prim.to}")
            if prim.item not in new_item_placement[prim.to]:
                raise IntegrityError(f"Item not in location: {prim.item}")
            new_item_placement[prim.to][prim.item] -= prim.count
            if new_item_placement[prim.to][prim.item] < 0:
                raise IntegrityError(f"Cannot remove more than available in location: {prim.item}")
            if new_item_placement[prim.to][prim.item] == 0:
                del new_item_placement[prim.to][prim.item]
                
        elif prim.type == "change_location":
            new_location = prim.to
            
        else:
            raise IntegrityError(f"Unknown primitive type: {prim.type}")
    
    return GameState(
        turn=new_turn,
        location=new_location,
        inventory=new_inventory,
        stats=new_stats,
        flags=new_flags,
        item_placement=new_item_placement,
        npc_locations=state.npc_locations  # статичен в MVP
    )

class ProjectionLookup:
    """Интерфейс чтения проекции для парсера, движка и сборщика промпта."""
    
    def __init__(self, state: GameState, canon: Canon):
        self.state = state
        self.canon = canon
    
    def current_location(self) -> str:
        return self.state.location
    
    def items_here(self, loc: Optional[str] = None) -> dict[str, int]:
        loc = loc or self.state.location
        return dict(self.state.item_placement.get(loc, {}))
    
    def npcs_here(self, loc: Optional[str] = None) -> list[str]:
        loc = loc or self.state.location
        return [npc_id for npc_id, npc_loc in self.state.npc_locations.items() if npc_loc == loc]
    
    def inventory(self) -> dict[str, int]:
        return dict(self.state.inventory)
    
    def has_item(self, item_type_id: str, min_count: int = 1) -> bool:
        return self.state.inventory.get(item_type_id, 0) >= min_count
    
    def flag(self, name: str) -> Any:
        return self.state.flags.get(name)
    
    def use_targets_here(self, loc: Optional[str] = None) -> list[str]:
        loc = loc or self.state.location
        result = []
        for ut_id, ut in self.canon.use_targets.items():
            if ut.exit and ut.exit[0] == loc:
                result.append(ut_id)
        return result
    
    def npc_location(self, npc_id: str) -> Optional[str]:
        return self.state.npc_locations.get(npc_id)

def replay(canon: Canon, turn_events: list[Event]) -> GameState:
    """Восстанавливает состояние из списка turn-событий. Порядок событий строго проверяется."""
    state = build_initial(canon)
    
    last_turn = -1
    for event in turn_events:
        if event.kind != "turn":
            continue
        if event.turn <= last_turn:
            raise IntegrityError(f"Events out of order: turn {event.turn} after {last_turn}")
        last_turn = event.turn
        
        state = GameState(
            turn=event.turn,
            location=state.location,
            inventory=state.inventory,
            stats=state.stats,
            flags=state.flags,
            item_placement=state.item_placement,
            npc_locations=state.npc_locations
        )
        if event.outcome:
            state = apply(state, list(event.outcome.applied))
    
    return state