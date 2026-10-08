# Dispatch v2: design proposal

Author: Platform team · Status: draft for review · Target launch: Q1

## 1. Goals

- Match riders to nearby drivers in under 3 seconds at the 95th percentile.
- Support the launch target of **50,000 concurrently online drivers** and
  **8,000 ride requests per minute** at peak across our three launch cities.
- Charge riders automatically at the end of each trip.

## 2. Non-goals

- Shared rides and scheduled rides (later phase).
- Multi-region deployment. We launch in one cloud region.

## 3. Architecture overview

Five services behind the API gateway:

| Service | Responsibility | Instances |
|---|---|---|
| Gateway | Auth, rate limiting (per user and per IP) | 4 |
| Location | Receives driver GPS updates | 6 |
| Matcher | Picks a driver for each ride request | 1 |
| Trips | Trip lifecycle, fares | 4 |
| Notifier | Push notifications to driver and rider apps | 2 |

All services share one PostgreSQL 16 primary (with one read replica for
analytics). Services talk to each other over synchronous HTTP.

## 4. Driver location

Driver apps send their GPS position every **2 seconds** while online. The
Location service writes each update straight to Postgres:

```sql
UPDATE drivers SET lat = $1, lng = $2, location_updated_at = now() WHERE id = $3;
INSERT INTO driver_locations (driver_id, lat, lng, recorded_at) VALUES ($3, $1, $2, now());
```

`driver_locations` keeps the full history for the analytics team's heat maps
and for resolving fare disputes. We have no plans to delete it.

## 5. Matching

The Matcher keeps an in-memory geospatial index of available drivers. On
startup it loads every online driver from `drivers` (about 4 minutes at launch
scale) and then refreshes from the table every 5 seconds.

For each ride request the Matcher:

1. finds the closest available driver in its index;
2. creates an offer row and sends the offer through the Notifier;
3. waits 15 seconds for the driver to accept;
4. if there is no answer, offers the trip to the next closest driver.

The driver app accepts with `POST /offers/{offer_id}/accept`, which runs:

```sql
UPDATE trips SET driver_id = $1, status = 'driver_assigned' WHERE id = $2;
```

The Matcher runs as a single instance so that two matchers can never offer
the same driver at the same time. Deploys restart it.

## 6. Ride requests

The rider app generates a `request_id` (UUID) for each ride request and
sends it with `POST /rides`. The Trips service stores it with a unique
constraint, so a retried request returns the existing ride instead of
creating a second one.

## 7. Trip completion and payment

When the driver ends the trip, the Trips service:

1. computes the fare from the route distance and time;
2. calls the Payments provider `POST /charges` with the rider's saved card and
   the fare;
3. on a timeout or a 5xx response, retries the charge up to 3 times, 2 seconds
   apart;
4. marks the trip `completed` and sends the receipt.

## 8. Notifications

The Notifier sends offers, arrival alerts and receipts through a single push
provider. Driver offers are delivered only by push.

## 9. Pricing

Surge multipliers are recomputed every minute by a job in the Trips service
from the last 5 minutes of ride requests per area, and cached in Redis for 60
seconds.

## 10. Operations

- **Deploys:** rolling, one instance at a time, through CI.
- **Backups:** a nightly `pg_dump` of the primary to object storage.
- **Monitoring:** we will build dashboards after launch, once we know which
  metrics matter. Until then each service logs to stdout.
- **Security:** drivers and riders authenticate with short-lived JWTs issued by
  the existing identity service. Card data never touches our servers; the
  Payments provider stores it.

## 11. Open questions

- Should the Location service batch its writes?
- Do we need a second push provider?
