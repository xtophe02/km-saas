"""Trip generation engine."""
import random
from datetime import datetime, timedelta, date
from typing import List, Dict, Tuple, Union
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.utils.distance import DistanceCalculator

logger = logging.getLogger(__name__)


class TripGenerator:
    """Generate trips based on office location and target kilometers."""

    def __init__(self, db: Session, office_address: str, target_kilometers: float,
                 table_name: str, num_days: int):
        self.office_address = office_address
        self.target_kilometers = round(target_kilometers, 2)
        self.table_name = table_name
        self.num_days = num_days
        self.distance_calculator = DistanceCalculator(db)

        # Load code sites from DB
        result = db.execute(
            text("SELECT code, address FROM code_sites WHERE table_name = :t"),
            {"t": table_name},
        ).fetchall()
        self.code_sites = [{"code": row[0], "address": row[1]} for row in result]

        if not self.code_sites:
            raise ValueError(f"No code sites found for table '{table_name}'")

        # Build distance matrix (loads cached distances into memory)
        site_addresses = [s["address"] for s in self.code_sites]
        self.distance_calculator.build_distance_matrix(office_address, site_addresses)

        # Pre-compute round-trip distances — limit to 500 sites max
        # For large tables, prefer sites that already have cached distances
        MAX_PRECOMPUTE = 500
        self._site_distances = []

        if len(self.code_sites) > MAX_PRECOMPUTE:
            cache = self.distance_calculator.memory_cache
            cached_sites = []
            uncached_sites = []
            for site in self.code_sites:
                key_out = f"{self.distance_calculator._normalize(office_address)}|{self.distance_calculator._normalize(site['address'])}"
                key_back = f"{self.distance_calculator._normalize(site['address'])}|{self.distance_calculator._normalize(office_address)}"
                if key_out in cache and key_back in cache:
                    cached_sites.append(site)
                else:
                    uncached_sites.append(site)

            sites_to_compute = cached_sites[:]
            remaining_slots = MAX_PRECOMPUTE - len(cached_sites)
            if remaining_slots > 0 and uncached_sites:
                sites_to_compute.extend(random.sample(uncached_sites, min(remaining_slots, len(uncached_sites))))

            logger.info(f"Large table ({len(self.code_sites)} sites): using {len(cached_sites)} cached + "
                       f"{len(sites_to_compute) - len(cached_sites)} sampled for pre-compute")
        else:
            sites_to_compute = self.code_sites

        for site in sites_to_compute:
            d_out = round(self._get_distance(office_address, site["address"]), 2)
            d_back = round(self._get_distance(site["address"], office_address), 2)
            if d_out > 0 and d_back > 0:
                self._site_distances.append({
                    "site": site,
                    "d_out": d_out,
                    "d_back": d_back,
                    "round_trip": round(d_out + d_back, 2),
                })
        self._site_distances.sort(key=lambda s: s["round_trip"])

        if self._site_distances:
            self._min_rt = self._site_distances[0]["round_trip"]
            self._max_rt = self._site_distances[-1]["round_trip"]
            self._avg_rt = sum(e["round_trip"] for e in self._site_distances) / len(self._site_distances)
        else:
            self._min_rt = self._max_rt = self._avg_rt = 0

    def _get_distance(self, start: str, end: str) -> float:
        distance, _ = self.distance_calculator.calculate_distance(start, end)
        return distance

    def _get_easter_date(self, year: int) -> date:
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    def _is_holiday(self, date_obj: Union[datetime, date]) -> bool:
        if isinstance(date_obj, datetime):
            date_obj = date_obj.date()
        if date_obj.weekday() >= 5:
            return True

        year = date_obj.year
        holidays = [
            date(year, 1, 1), date(year, 5, 1), date(year, 7, 21),
            date(year, 8, 15), date(year, 11, 1), date(year, 11, 11),
            date(year, 12, 25),
        ]
        easter = self._get_easter_date(year)
        holidays += [
            easter, easter + timedelta(days=1),
            easter + timedelta(days=39), easter + timedelta(days=50),
        ]
        return date_obj in holidays

    def _is_workday(self, d: Union[datetime, date]) -> bool:
        check = d.date() if isinstance(d, datetime) else d
        return check.weekday() < 5 and not self._is_holiday(check)

    def _make_trip(self, trip_date: datetime, entry: Dict) -> Dict:
        """Create a trip using real cached distances (no adjustment)."""
        site = entry["site"]
        return {
            "date": trip_date.strftime("%Y-%m-%d"),
            "start_address": self.office_address,
            "sites": [{
                "code": site["code"],
                "address": site["address"],
                "distance": entry["d_out"],
            }],
            "return_distance": entry["d_back"],
            "total_distance": entry["round_trip"],
        }

    def _make_multi_site_trip(self, trip_date: datetime, site_entries: list) -> Dict:
        """Create a trip visiting multiple sites with real distances.
        Route: office -> site1 -> site2 -> ... -> office
        """
        sites_data = []
        total = 0.0

        for idx, entry in enumerate(site_entries):
            if idx == 0:
                dist = entry["d_out"]  # office -> first site
            else:
                # site_prev -> site_current: get real distance
                prev_addr = site_entries[idx - 1]["site"]["address"]
                cur_addr = entry["site"]["address"]
                dist = round(self._get_distance(prev_addr, cur_addr), 2)
                if dist <= 0:
                    dist = entry["d_out"]  # fallback

            sites_data.append({
                "code": entry["site"]["code"],
                "address": entry["site"]["address"],
                "distance": round(dist, 2),
            })
            total += dist

        # Return from last site to office
        return_dist = site_entries[-1]["d_back"]
        total += return_dist

        return {
            "date": trip_date.strftime("%Y-%m-%d"),
            "start_address": self.office_address,
            "sites": sites_data,
            "return_distance": round(return_dist, 2),
            "total_distance": round(total, 2),
        }

    def _make_adjusted_trip(self, trip_date: datetime, entry: Dict) -> Dict:
        """Create a trip with real outbound and return padded by 5-15%.
        Return leg is always >= real distance, never shrunk.
        """
        site = entry["site"]
        d_out = entry["d_out"]
        padding = random.uniform(0.05, 0.15)
        adjusted_return = round(entry["d_back"] * (1 + padding), 2)
        return {
            "date": trip_date.strftime("%Y-%m-%d"),
            "start_address": self.office_address,
            "sites": [{
                "code": site["code"],
                "address": site["address"],
                "distance": d_out,
            }],
            "return_distance": adjusted_return,
            "total_distance": round(d_out + adjusted_return, 2),
        }

    def _find_closing_site(self, remaining: float) -> Dict:
        """Find the site whose real round-trip is closest to remaining.
        The padded trip (return +5-15%) will slightly overshoot — that's fine.
        Returns the best site entry, or None if no sites available.
        """
        best_site = None
        best_diff = float("inf")

        for entry in self._site_distances:
            if entry["round_trip"] <= 0:
                continue
            # The actual trip will be d_out + d_back*(1.05~1.15)
            # Use midpoint (1.10) for estimation
            estimated_total = entry["d_out"] + entry["d_back"] * 1.10
            diff = abs(estimated_total - remaining)
            if diff < best_diff:
                best_site = entry
                best_diff = diff

        return best_site

    def generate_trips(self, month: int, year: int, km_limit: float = None) -> List[Dict]:
        """Generate trips to exactly match target kilometers.

        Args:
            month: Month number (1-12)
            year: Year
            km_limit: Optional km cap (for free tier).
        """
        try:
            effective_target = km_limit if km_limit and km_limit < self.target_kilometers else self.target_kilometers

            if not self._site_distances:
                logger.error("No valid site distances available")
                return []

            # Get workdays in the month
            month_start = datetime(year, month, 1)
            if month == 12:
                next_month = datetime(year + 1, 1, 1)
            else:
                next_month = datetime(year, month + 1, 1)
            month_end = next_month - timedelta(days=1)

            workdays = []
            current = month_start
            while current <= month_end:
                if self._is_workday(current):
                    workdays.append(current)
                current += timedelta(days=1)

            # Pick num_days random workdays
            days_to_use = min(self.num_days, len(workdays))
            selected_days = sorted(random.sample(workdays, days_to_use))

            if days_to_use < 2:
                logger.error("Not enough workdays to generate trips")
                return []

            # Free tier: generate trips up to km_limit, no exact matching needed
            if km_limit and km_limit < self.target_kilometers:
                trips = []
                total = 0.0
                for day in selected_days:
                    site = random.choice(self._site_distances)
                    if total + site["round_trip"] > km_limit:
                        break
                    trips.append(self._make_trip(day, site))
                    total += site["round_trip"]
                logger.info(
                    f"Generated {len(trips)} trips, {total:.2f}km (free tier limit: {km_limit}km)"
                )
                return trips

            # === Exact matching strategy ===
            target = effective_target

            # --- Step 1: Determine how many trips we want ---
            estimated_trips = max(2, round(target / self._avg_rt))
            num_trips = min(estimated_trips, len(workdays))
            num_trips = max(2, num_trips)

            # Determine if we need multi-site trips (2 sites per day)
            need_per_day = target / min(num_trips, len(workdays))
            use_multi_site = need_per_day > self._max_rt * 0.7

            # Select workdays spread across the month
            if num_trips >= len(workdays):
                selected_workdays = workdays[:]
                num_trips = len(workdays)
            else:
                step = len(workdays) / num_trips
                selected_workdays = [workdays[int(i * step)] for i in range(num_trips)]

            # --- Step 2: Add real trips, last trip gets padded return (+5-15%) ---
            trips = []
            total_distance = 0.0
            ideal_per_trip = target / num_trips

            for i in range(len(selected_workdays)):
                remaining = round(target - total_distance, 2)
                is_last_trip = (i == len(selected_workdays) - 1) or (len(trips) >= num_trips - 1)

                # Last trip: use closest site with padded return
                if is_last_trip and len(trips) >= 1:
                    candidate = self._find_closing_site(remaining)
                    if candidate:
                        last_trip = self._make_adjusted_trip(selected_workdays[i], candidate)
                        trips.append(last_trip)
                        total_distance += last_trip["total_distance"]
                    break

                # Ensure we don't overshoot — leave room for at least one more trip
                max_allowed = remaining - self._min_rt * 0.5
                if max_allowed <= 0:
                    candidate = self._find_closing_site(remaining)
                    if candidate:
                        last_trip = self._make_adjusted_trip(selected_workdays[i], candidate)
                        trips.append(last_trip)
                        total_distance += last_trip["total_distance"]
                    break

                # Find candidates close to ideal_per_trip
                candidates = [
                    e for e in self._site_distances
                    if e["round_trip"] <= max_allowed
                    and ideal_per_trip * 0.5 <= e["round_trip"] <= ideal_per_trip * 1.5
                ]
                if not candidates:
                    candidates = [
                        e for e in self._site_distances
                        if e["round_trip"] <= max_allowed
                    ]
                if not candidates:
                    candidate = self._find_closing_site(remaining)
                    if candidate:
                        last_trip = self._make_adjusted_trip(selected_workdays[i], candidate)
                        trips.append(last_trip)
                        total_distance += last_trip["total_distance"]
                    break

                # For high targets, use multi-site trips (2 sites per day)
                if use_multi_site and len(candidates) >= 2:
                    random.shuffle(candidates)
                    site_a = candidates[0]
                    best_b = None
                    best_diff = float("inf")
                    for c in candidates[1:20]:
                        combined = site_a["d_out"] + c["d_out"] + c["d_back"]
                        diff = abs(combined - ideal_per_trip)
                        if diff < best_diff:
                            best_b = c
                            best_diff = diff
                    if best_b:
                        trip = self._make_multi_site_trip(selected_workdays[i], [site_a, best_b])
                        if trip["total_distance"] <= max_allowed:
                            trips.append(trip)
                            total_distance += trip["total_distance"]
                            continue
                    site_entry = random.choice(candidates)
                elif ideal_per_trip > self._avg_rt * 1.2:
                    candidates.sort(key=lambda e: e["round_trip"], reverse=True)
                    top_n = max(1, len(candidates) // 3)
                    site_entry = random.choice(candidates[:top_n])
                else:
                    site_entry = random.choice(candidates)

                trip = self._make_trip(selected_workdays[i], site_entry)
                trips.append(trip)
                total_distance += trip["total_distance"]

            trip_list = sorted(trips, key=lambda x: x["date"])

            final_total = round(sum(t["total_distance"] for t in trip_list), 2)
            logger.info(
                f"Generated {len(trip_list)} trips, {final_total:.2f}km (target: {target}km)"
            )
            return trip_list

        except Exception as e:
            logger.error(f"Error generating trips: {e}")
            return []
