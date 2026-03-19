"""Distance calculation with 3-tier cache: memory -> PostgreSQL -> Google Maps API."""
import logging
from typing import Tuple, List, Optional
import googlemaps
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.config import get_settings

logger = logging.getLogger(__name__)


class DistanceCalculator:
    """Calculate distances between addresses using Google Maps API with caching."""

    @staticmethod
    def _normalize(addr: str) -> str:
        """Normalize address for consistent cache keys."""
        import re
        addr = addr.strip().lower()
        addr = re.sub(r"\s+", " ", addr)
        return addr

    def __init__(self, db: Session):
        settings = get_settings()
        self.gmaps = googlemaps.Client(key=settings.google_maps_api_key)
        self.db = db
        self.memory_cache = {}
        self.api_calls = 0

    def _get_cached_distance(self, origin: str, destination: str) -> Optional[float]:
        try:
            result = self.db.execute(
                text("""
                    SELECT distance_km FROM distances
                    WHERE (origin = :o AND destination = :d)
                    OR (origin = :d AND destination = :o)
                    LIMIT 1
                """),
                {"o": origin, "d": destination},
            ).fetchone()
            return float(result[0]) if result else None
        except Exception as e:
            logger.error(f"Error getting cached distance: {e}")
            return None

    def _cache_distance(self, origin: str, destination: str, distance: float):
        try:
            rounded = round(distance, 2)
            # Insert both directions
            self.db.execute(
                text("""
                    INSERT INTO distances (origin, destination, distance_km)
                    VALUES (:o, :d, :dist)
                    ON CONFLICT (origin, destination) DO UPDATE SET distance_km = :dist
                """),
                {"o": origin, "d": destination, "dist": rounded},
            )
            self.db.execute(
                text("""
                    INSERT INTO distances (origin, destination, distance_km)
                    VALUES (:o, :d, :dist)
                    ON CONFLICT (origin, destination) DO UPDATE SET distance_km = :dist
                """),
                {"o": destination, "d": origin, "dist": rounded},
            )
            self.db.commit()
            logger.info(f"Cached: {origin} <-> {destination} = {rounded:.2f}km")
        except Exception as e:
            logger.error(f"Error caching distance: {e}")
            self.db.rollback()

    def calculate_distance(self, origin: str, destination: str) -> Tuple[float, bool]:
        origin = self._normalize(origin)
        destination = self._normalize(destination)
        cache_key = f"{origin}|{destination}"

        # Tier 1: memory cache
        if cache_key in self.memory_cache:
            return self.memory_cache[cache_key], True

        # Tier 2: database cache
        cached = self._get_cached_distance(origin, destination)
        if cached is not None:
            self.memory_cache[cache_key] = cached
            return cached, True

        # Tier 3: Google Maps API
        try:
            self.api_calls += 1
            result = self.gmaps.distance_matrix(
                origins=[origin],
                destinations=[destination],
                mode="driving",
                units="metric",
            )

            if result["status"] == "OK":
                element = result["rows"][0]["elements"][0]
                if element["status"] == "OK":
                    distance = element["distance"]["value"] / 1000
                    self._cache_distance(origin, destination, distance)
                    self.memory_cache[cache_key] = distance
                    return distance, True
                elif element["status"] == "OVER_QUERY_LIMIT":
                    logger.error("Google Maps API quota exceeded")
                    return 10.0, False
            elif result["status"] in ("OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"):
                logger.error(f"Google Maps API quota exceeded: {result['status']}")
                return 10.0, False

            logger.warning(f"Invalid Google Maps response: {result}")
            return 10.0, False

        except Exception as e:
            logger.error(f"Error calculating distance: {e}")
            return 10.0, False

    def _load_all_cached_distances(self, addresses: List[str]) -> dict:
        cached = {}
        try:
            # Build parameterized query
            params = {f"a{i}": addr for i, addr in enumerate(addresses)}
            placeholders = ", ".join(f":a{i}" for i in range(len(addresses)))
            result = self.db.execute(
                text(f"""
                    SELECT origin, destination, distance_km FROM distances
                    WHERE origin IN ({placeholders}) AND destination IN ({placeholders})
                """),
                params,
            ).fetchall()
            for row in result:
                cached[f"{row[0]}|{row[1]}"] = float(row[2])
        except Exception as e:
            logger.error(f"Error loading cached distances: {e}")
        return cached

    def build_distance_matrix(self, office_address: str, site_addresses: List[str]) -> None:
        logger.info("Building distance matrix...")
        office_address = self._normalize(office_address)
        site_addresses = [self._normalize(a) for a in site_addresses]
        all_addresses = [office_address] + site_addresses

        if len(all_addresses) > 100:
            cached = self._load_all_cached_distances(all_addresses)
            for key, dist in cached.items():
                self.memory_cache[key] = dist
            logger.info(f"Loaded {len(cached)} cached distances into memory")
            return

        cached = self._load_all_cached_distances(all_addresses)
        logger.info(f"Found {len(cached)} cached distances")

        uncached_origins = set()
        for origin in all_addresses:
            for destination in all_addresses:
                if origin != destination:
                    cache_key = f"{origin}|{destination}"
                    if cache_key in cached:
                        self.memory_cache[cache_key] = cached[cache_key]
                    else:
                        uncached_origins.add(origin)

        if not uncached_origins:
            logger.info("All distances already cached")
            return

        uncached_origins = list(uncached_origins)
        logger.info(f"Fetching distances from {len(uncached_origins)} uncached origins")

        BATCH = 10
        try:
            for i in range(0, len(uncached_origins), BATCH):
                origin_batch = uncached_origins[i : i + BATCH]
                for j in range(0, len(all_addresses), BATCH):
                    dest_batch = all_addresses[j : j + BATCH]
                    self.api_calls += 1
                    result = self.gmaps.distance_matrix(
                        origins=origin_batch,
                        destinations=dest_batch,
                        mode="driving",
                        units="metric",
                    )

                    if result["status"] == "OK":
                        for oi, row in enumerate(result["rows"]):
                            origin = origin_batch[oi]
                            for di, element in enumerate(row["elements"]):
                                destination = dest_batch[di]
                                if element["status"] == "OK":
                                    dist = element["distance"]["value"] / 1000
                                    self._cache_distance(origin, destination, dist)
                                    self.memory_cache[f"{origin}|{destination}"] = dist
                                    self.memory_cache[f"{destination}|{origin}"] = dist
                                elif element["status"] == "OVER_QUERY_LIMIT":
                                    logger.error("Quota exceeded in matrix build")
                                    return
                    elif result["status"] in ("OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"):
                        logger.error(f"Quota exceeded: {result['status']}")
                        return
        except Exception as e:
            logger.error(f"Error building distance matrix: {e}")
