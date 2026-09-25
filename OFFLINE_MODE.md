# KORKO degraded mode

The station process serves the customer-only local API on **TCP port 9100**.
Set `KORKO_OFFLINE_PORT` to change it. The mobile browser derives the station
host from the cloud page's hostname and connects to that host on port 9100.

## Local API

- `GET /offline/status` — station A, KORKO time, available HOME boards, and
  local offline rental states.
- `GET /offline/locations` — offline rental states.
- `POST /offline/arme` — JSON `{ "authorization": "…", "station": "A" }`;
  returns the assigned board and offline reservation ID.

The service only exposes these local customer operations. It does not expose
cloud or administration operations. The station refreshes its board and
maintenance snapshot from the cloud every five seconds and keeps the last
snapshot when the cloud is unavailable.

## Authorization and persistence

After a successful online `/api/arme`, the cloud returns an opaque signed
bearer token. Its base64url JSON payload contains `authorization_id`,
`station_id`, and a 30-day Unix `expires` value. The browser stores that token
in local storage; it contains no phone, card, or payment data. The cloud and
station use `KORKO_OFFLINE_SECRET`, which defaults to a prototype secret and
should be set to the same private value in both processes for a real setup.

The station appends NDJSON records to `station_journal.ndjson` (override with
`KORKO_STATION_JOURNAL`). Offline reservation records use
`OFFLINE_RESERVATION` with the authorization ID, offline reservation ID,
board, station, KORKO `t`, and status. The existing physical `DEPART` and
`RETOUR` records retain their original KORKO `t` and receive stable event IDs.
The station's `station_offline_state.json` (override with
`KORKO_STATION_STATE`) preserves board, maintenance, and offline rental state
when journal entries have already reached the cloud.

The station drains the journal in insertion order once the cloud responds.
That naturally sends each offline reservation ahead of its physical events.
The cloud stores received event IDs and ignores repeats, then uses original
DEPART and RETOUR `t` values for rental duration and final amount. The browser
polls cloud state and falls back to local rental state while disconnected.

## Manual verification

1. Start `mon_cloud.py`, `ma_station.py`, and `korko_sim.py`; open the customer
   UI from the station host. Complete and return one online rental, return to
   the ready screen, and confirm `korko-offline-authorization` appears in
   browser local storage.
2. Stop `mon_cloud.py`, leave station and simulator running, and select
   **Je veux surfer**. The UI should show **Connexion limitée** and
   **Prendre une planche**. Confirm the board number appears without a failed
   cloud POST. Trigger DEPART and RETOUR in the simulator.
3. Restart `mon_cloud.py`. Confirm the cloud customer state reaches returned,
   the amount uses original event timestamps, and replaying the journal does
   not create another rental.
4. In a fresh browser profile with no `korko-offline-authorization`, stop the
   cloud and try to rent. The UI should explain that a first connection is
   required and leave the action disabled.
5. Put one HOME board in maintenance while the cloud is online, allow the
   station snapshot to refresh, then stop the cloud. Reserve offline and
   confirm the maintenance board is never assigned.
6. With cloud still down and an offline reservation plus physical events
   pending, restart `ma_station.py`. Confirm `/offline/status` retains the
   reservation and its state, and that cloud replay still completes it after
   the cloud returns.

## Prototype limits

The token uses a shared prototype HMAC secret and local HTTP, so this is not a
production security boundary. Offline payment, account creation, long-term
billing, and condition reports remain cloud-only. Maintenance freshness is
limited to the last cloud snapshot available to the station.
