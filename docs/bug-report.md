# Bug report

Audit of the Vehicle Flow Simulator, 14 November 2026. It covers the engine, scenario
builder, API, run store and frontend.

- **Fixed** means fixed in this round of work and covered by a test or a measurement.
- **Open** means the bug is still in the code.
- "How confirmed" says whether the bug was seen in a run or found by reading the code.

Measurements use the seeded Silk Board graph (`12.9097_77.6175_12.9223_77.6304`), 09:00,
600 s, seed 42.

## Summary

| Severity | Fixed | Open |
|----------|-------|------|
| High     | 7     | 2    |
| Medium   | 8     | 6    |
| Low      | 4     | 7    |

Collision numbers before and after, moderate traffic, with the original 3 signals so the two
columns are comparable:

| Measure (600 s) | Before | After |
|-----------------|--------|-------|
| Vehicles overlapping the one ahead in their lane (samples) | 245 | 0 |
| Vehicle pairs drawn closer than 1.2 m | 1,646 | 221 |
| Steps with braking harder than 4 m/s² | 2,607 | 438 |
| Throughput (veh/h) | 6,090 | 5,925 |

## Fixed

| ID | Sev. | Area | Bug | Where | How confirmed |
|----|------|------|-----|-------|---------------|
| F1 | High | Engine | A junction grant was never re-checked. A vehicle granted while far away drove into a box that another vehicle had since entered. | `sim/junctions.py` `_revalidate_grants` | Measured (overlap counts) |
| F2 | High | Engine | Gap acceptance compared the other vehicle's arrival with a fixed critical gap and ignored how long the entering vehicle needs to clear the conflict point. Trucks entered in front of arriving traffic. | `sim/junctions.py` `_conflict_blocked` | Measured |
| F3 | High | Engine | Vehicles on different turn paths that share road space could not see each other: paths merging into one out-lane, paths leaving one in-lane, and several lanes feeding a narrower turn path. | `sim/leaders.py` `junction_queues` | Traced vehicle by vehicle |
| F4 | High | Engine | Nothing stopped a vehicle overlapping its leader after a hand-over between links. | `sim/engine.py` `_separate` | Test `test_no_vehicle_overlaps_its_leader` |
| F5 | High | Engine | Followers ran red lights. Only the first vehicle in a lane obeyed the signal; a follower became first too late to stop. | `sim/junctions.py` `must_stop_at_signal` | Test `test_no_vehicle_enters_a_junction_on_red` |
| F6 | High | Engine | Vehicles started slowing for a turn from the far end of the road, because the turn-speed limit was applied at any distance. | `sim/engine.py` `_accelerations` | Code reading, then measured |
| F7 | High | Geometry | Junction boxes were too small where roads meet at a shallow angle, so the ends of two different roads were drawn on top of each other and waiting vehicles overlapped. | `sim/network_parts.py` `junction_radius` | Measured (947 → 218 pairs) |
| F8 | Med. | Engine | Lane changes needed only a 0.5 m gap and checked safety for the new follower only, so the changer often had to brake hard. | `sim/mobil.py` `_evaluate` | Hard-brake classification |
| F9 | Med. | Recorder | A lane change was drawn as a 3.2 m sideways jump. The `mobil.py` docstring said the recorder smooths it; it did not. | `sim/recorder.py` `_lateral`, agent field `lat` | Test `test_lane_change_is_drawn_as_a_drift` |
| F10 | Med. | Recorder | Vehicles jumped sideways entering and leaving a junction when the turn path had fewer lanes than the road. | `sim/recorder.py` `_lateral` | Code reading |
| F11 | Med. | Engine | A vehicle entering a short road at speed met a stop line it could not stop for (spawned vehicles too). | `sim/leaders.py` `stop_line_distance`, `sim/spawn.py` | Hard-brake classification |
| F12 | Med. | Engine | Lane changers whose rear was still at the start of a road were hit by vehicles arriving from the junction behind. | `sim/mobil.py` `lane_changes` | Traced |
| F13 | Med. | Scenario | Junction labels (`J#`) were renumbered when a scenario changed the set of junctions, so per-junction baseline and scenario numbers did not line up. | `sim/network_parts.py` `labels_for` | Test `test_split_keeps_edge_ids_and_junction_labels` |
| F14 | Med. | API | A malformed `close_edges` entry crashed the whole run with `ValueError` instead of being rejected. | `api/schemas.py` `EdgeId` | Test (422) |
| F15 | Med. | Config | The area cap was 3.5 km × 3.5 km (12 km²), not 2 km². | `config.py` `max_half_side_m` | Code reading |
| F16 | Low | Frontend | A network download that finished after a newer run had started replaced the newer run's map. | `lib/runs.js` `poll` | Code reading |
| F17 | Low | Frontend | Pending edits stayed after the place was changed and were then silently dropped by the server. | `components/ParamsPanel.svelte` `apply` | Code reading |
| F18 | Low | Frontend | Closed lane numbers were sorted as text (`10` before `2`). | `components/RoadEditor.svelte` | Code reading |
| F19 | Low | Frontend | The map could open at maximum zoom when it was laid out after the network arrived. | `lib/maplayers.js` `relock` | Seen in the browser |

## Open

| ID | Sev. | Area | Bug | Where | Impact | How confirmed |
|----|------|------|-----|-------|--------|---------------|
| O1 | High | Engine / signals | Signals at every crossing junction with equal greens cut network throughput by about a third at light traffic (Silk Board, "less": 4,250 → 2,910 veh/h, mean speed 27.6 → 15.6 km/h, 195 signals instead of 3). This is the requested behaviour, listed here because of its size. | `sim/signals.py` | Every run is slower than before; the stop duration and the junction editor are the levers. | Measured |
| O2 | High | Storage | Run folders are never deleted. On Cloud Run the disk is memory-backed, so memory grows with every run until the instance restarts. | `runs/store.py` | Instance can run out of memory. | Code reading |
| O3 | Med. | Engine | A large junction that OSM maps as several nodes gets one independent signal per node. The signals are not coordinated, so vehicles stop inside the junction. | `sim/network.py` `_node_kinds` | Extra delay and queues at big junctions. | Code reading |
| O4 | Med. | Engine | Vehicles are still drawn closer than 1.2 m where two OSM roads run together near a junction (about 330 pair-samples in 600 s at moderate traffic). They are on different roads, so the engine sees no conflict. | Geometry from OSM; `MAX_RADIUS_M` | Visible overlap at a few places. | Measured |
| O5 | Med. | Engine | A circle can starve one entry under heavy flow; 1–4 vehicles per 15 minutes are then removed by the gridlock breaker. | `sim/ring.py` `entry_blocked` | Slight undercount of delay at busy circles. | Measured on the test grid |
| O6 | Med. | Scenario | Lane closures added as separate entries that together close every lane at one place are not recognised as a closed road. Vehicles queue until the gridlock breaker removes them. | `sim/network_parts.py` `closures_of` | Wrong results for that scenario. | Code reading |
| O7 | Med. | Frontend | A failed frame-chunk download is retried on every animation frame (about 60 requests per second) while the player waits. | `lib/frames.js` `load`, `frame` | Request storm when the server is down. | Code reading |
| O8 | Med. | Frontend | The space bar toggles playback even when a button has focus, and blocks that button's own activation. | `components/Playback.svelte` `onKey` | Keyboard users cannot press buttons with Space. | Code reading |
| O9 | Low | Frontend | Lane closures are not drawn on the map; only fully closed roads are. | `lib/maplayers.js` | The user cannot see a pending lane closure. | Code reading |
| O10 | Low | Engine | Hard braking still happens about 590 times in 600 s at moderate traffic (1,000 vehicles), mostly at the start of roads after a junction. | `sim/leaders.py` | Occasional abrupt stops. | Measured |
| O11 | Low | API | Without an `X-Session-Id` header the worker is chosen by client address. Behind the Cloud Run proxy, clients without the header can share one worker. | `api/routes.py` `session_key` | Only affects clients other than the bundled frontend. | Code reading |
| O12 | Low | Geocoder | The SQLite connection of the geocode cache is opened on every call and never closed. | `geo/geocoder.py` `_cached_get` | Small resource leak in long-lived workers. | Code reading |
| O13 | Low | Demand | An exit's attraction is taken from the first road arriving at it, even if that road is closed. | `sim/demand.py` `_in_edge` | Slightly wrong destination shares in closure scenarios. | Code reading |
| O14 | Low | Store | Progress assumes one recorded frame per second. | `runs/store.py` `progress` | Wrong progress bar if `frame_dt` is ever changed. | Code reading |
| O15 | Low | Engine | The lane index is rebuilt three to four times per step. | `sim/engine.py` `step` | Slower runs, no wrong results. | Code reading |

## Not bugs, but worth knowing

- **Stop duration floor.** Green is never under 10 s. A junction with 4 approaches therefore
  has at least 47 s of red, even if a smaller stop duration is entered.
- **Extra lane and routes.** An extra lane follows the shortest route between the two points
  over existing roads. If no road joins the points, the run stops with a plain message.
- **A closure name that matches nothing** used to give a scenario identical to the baseline
  with no hint. The results now say "no matching road in this area: nothing was closed".
