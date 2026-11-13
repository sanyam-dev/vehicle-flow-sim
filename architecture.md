# Vehicle Flow Simulator — Architecture

Language in, two seeded runs out, compared side by side.

```mermaid
flowchart TD
  subgraph U[1 · Understand]
    Q([User query<br/>natural language])
    A[Task Understanding Agent<br/>LLM · keeps chat context]
    P[/Query Params<br/>Pydantic-validated/]
    Q <-->|query / clarify| A
    A --> P
  end

  subgraph B[2 · Build scenario]
    G[Geocoder<br/>place → bounding box]
    GG[Graph Generator<br/>Overpass → Geofabrik extract fallback<br/>osmnx · cached per area]
    GM[Graph Modifier<br/>validate edits · re-route]
    ME[Map editor UI<br/>select nodes → add / close]
    G --> GG
    GG -->|base graph| GM
    ME -->|UI edits| GM
  end

  subgraph S[3 · Simulate]
    EB[Engine · baseline<br/>IDM · MOBIL · signals]
    D[Demand + Spawn<br/>same seed for both runs]
    ES[Engine · scenario<br/>IDM · MOBIL · signals]
    D --> EB
    D --> ES
  end

  subgraph V[4 · Store & serve]
    RS[(Run Store<br/>frames + metrics)]
    API[FastAPI<br/>/run · /status · /frames · /metrics]
    FE([Frontend<br/>replay + side-by-side compare])
    RS --> API --> FE
  end

  P -->|place name| G
  P -->|LLM edits| GM
  P -->|traffic + time| D
  GG --> EB
  GM -->|modified graph| ES
  EB --> RS
  ES --> RS
```

## Flow in words

1. **Understand**: the user query goes to the Task Understanding Agent (LLM), which asks clarifying questions when needed and outputs Pydantic-validated Query Params.
2. **Build scenario**: the place name goes to the Geocoder, which gives a bounding box, then the Graph Generator builds the base graph (cached per area). Roads come from the public Overpass API (time-limited; servers that fail are skipped); when Overpass fails, the area is cut with `osmium` from a roads-only Geofabrik extract (all of India: six zone files plus a whole-India file for areas on zone borders) kept in a Cloud Storage bucket mounted on Cloud Run. Both sources give the same OSM tags, and osmnx turns the OSM XML into the graph. Edits from the LLM or the map editor UI go to the Graph Modifier, which validates them and re-routes, producing the modified graph.
3. **Simulate**: traffic level and time of day set Demand + Spawn. Two engine runs use the same seed: baseline on the base graph, scenario on the modified graph.
4. **Store & serve**: frames and metrics go to the Run Store, which is served through FastAPI (`/run`, `/status`, `/frames`, `/metrics`). The frontend replays both runs side by side.
