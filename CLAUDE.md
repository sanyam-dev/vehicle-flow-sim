# Vehicle Flow Simulator


## Features: 

1. ### Task Understanding Agent & Chat UI
    - Have a task understanding agent to chat and get query from user via natural languange.
    - The Agent should have a context of the conversation.
    - Agent needs to store following data : 
        - Location Specifications: Box borders, area, subarea
        - Amount of traffic, per main road (one direction): less = 500-700 veh/h, moderate = 1000-1400 veh/h, high = about 2000+ veh/h
        - Timne of the day: correlated to amount of traffic (business hours should have higer spwan rate and night time should have very less spawn rate)


2. ### Simulation Display
    - Should display a grid for the area that is asked in the query.
    - Should have a toggle for satellite image layout that can be used for reference
    - Should have clear markings for each road and each junction for easy scenario creation.
    - Bridges/Underpasses should be of different colors
    - Use vector images to show bikes, autos, cars & trucks.
    - The display should have a clear legend
    - The display should be interactive : The user can select two nodes and should then be able to add a road, bridge or underpass, close a road, bridge or underpass (partly from one side or fully or closing one direction or closing specific lanes)

3. ### Scenario Creator:
    - Should be able to create scenario from user's language query.
    - if query doesn't have a scenario specification where graph edges are altered, e.g. : "simulate traffic at Silk Road", skip this

4. ### Simulation Engine:
    - Should be using Agent Based Modelling to model an agent in traffic
    - Each agent is it's own vehicle with it's own max speed, spawn position, type of vehicle etc.
    - Agents should be spawning at each of the entry node at predetermined spawn rate which will be based on the amount of expected traffic.
    - Agents should be simulating real-life based scenarios. Bikes and autos should be changing lanes.
    - Agent should have lane-changing model (MOBIL) and car-following model (IDM)
    - Junction control : signals, priority, conflict resolution
    - Routes: agents need a destination and a path. Without one they just make random turns.
    - Road network comes from OSM via osmnx. Lane count comes from the lanes tag, with defaults by road type when it’s missing.
    - Lanes determine capacity. Capacity should emerge from IDM and MOBIL, not be hard-coded.
    - Flyovers and underpasses (bridge / tunnel / layer tags) are grade-separated: no conflict where they cross other roads, and merges and diverges at the ramps.
    - a fixed random seed

5. ### Metrics:
    - Have a metric tab on the right of simualation display
    - Metrics: Travel time, throughput, queue length, delay per junction, speed heatmap
    - baseline vs. scenario comparison

## Architecture: 
Check `./architecture.md` for reference

## Dependencies: 

- Host : Google Cloud Run (one service: FastAPI serves the API and the built frontend)
- Maps : Leaflet
- Metrics : uPlots
- UI : Svelte + Vite
- Simulations: Python + FastAPI
- LLM : Ollama Cloud


## Dos and Don'ts
- Don;t let task understanding LLM produce coordinate. Use a geocoder service (OSM)
- Precompute and cache routes (shortest paths with networkx). Recompute only the affected routes when a road is closed.
- For results, run the baseline and the scenario in fast mode without streaming, then compare the metrics.
- Give each user’s simulation its own worker process, so one heavy run doesn’t block the others.
- Keep each function under 70 lines and keep the code very readible. Put comments and arguements specs
- Store agents as numpy arrays, one array per attribute, rather than one Python object each
Cache the OSM graph for each area, since downloads are slow
- Respect Nominatim’s usage limits (about 1 request per second) and cache geocoding results
Keep the LLM provider swappable and validate its output with Pydantic
- Remember, always ask me in doubt.