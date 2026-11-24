# PubliBike tracker

Polls PubliBike/Velospot availability every minute and stores changes in Postgres, to study how bikes move around Zürich (and the rest of Switzerland).

Runs on `ben-docker-1` in `~/projects/publibike-tracker` (`docker compose up -d --build`).

## Sources
- **Live counts** (every minute): the app's public station list, `velospot.info/customer/public/api/pbvsng/stations`. Undocumented and unofficial. No key needed.
- **Official GBFS** from [sharedmobility.ch](https://github.com/SFOE/sharedmobility) (Swiss Federal Office of Energy): `free_bike_status`, `station_status`, `station_information`. It updates only about every 15 min. Bike IDs change after every trip, so they can't follow a single bike. Licence: open use, source must be credited.

## Tables
- `live_station_events`: a row whenever a station's live counts change. `t1`..`t6` are counts per vehicle type: 1 bike, 2 e-bike, 3 bike with your own battery, 4 cargo bike, 5 e-scooter.
- `station_events`, `bike_events`: changes in the GBFS feeds. `bike_events.kind` is `appear`, `change` or `gone`.
- `stations`: names, coordinates, capacity and the raw GBFS record (`raw->>'region_id' = '36'` is Zürich).
- `fetches`: one row per poll, including errors (use it to find gaps).

Raw responses are archived gzipped in `raw/YYYY-MM-DD/`.

## Analysis access
```sh
ssh -L 55441:127.0.0.1:55441 ben-docker-1
psql postgresql://publibike:publibike@localhost:55441/publibike
```
