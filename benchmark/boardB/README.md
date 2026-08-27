# Board B candidates

Empty by design in this cycle: the generation framework (constraint
schema in the toolkit's pcbqa/placement.py, the optimizer loop
evaluated in docs/placement_routing.md) exists, and candidates will be
generated into timestamped subdirectories here. No candidate is ever
the authoritative board; promotion is an explicit human decision.

What remains before a fair A/B comparison:
1. a constraint set expressing this board's semantic floorplan;
2. a scripted place_optimize -> route -> score loop on a candidate
   copy (KiCadRoutingTools, evaluated and installed);
3. candidate validation through the same toolkit gates as Board A;
4. metric extraction with the shared schema and the same baseline
   command Board A used.
