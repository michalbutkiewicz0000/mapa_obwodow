from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

Parity = Literal["odd", "even", "all"]


@dataclass
class NumberRange:
    start: int
    end: int | None  # None = do końca

    def contains(self, number: int) -> bool:
        if number <= 0:
            return False
        if self.end is None:
            return number >= self.start
        return self.start <= number <= self.end


@dataclass
class StreetRule:
    name: str
    parity: Parity = "all"
    ranges: list[NumberRange] = field(default_factory=list)

    def matches(self, street: str, number: int | None) -> bool:
        if not streets_equal(self.name, street):
            return False
        if number is None:
            return not self.ranges and self.parity == "all"
        if self.parity == "odd" and number % 2 == 0:
            return False
        if self.parity == "even" and number % 2 == 1:
            return False
        if not self.ranges:
            return True
        return any(rng.contains(number) for rng in self.ranges)

    def specificity(self) -> int:
        """Im wyższa wartość, tym bardziej precyzyjna reguła (używane przy rozstrzyganiu konfliktów)."""
        if self.ranges:
            return 2
        if self.parity != "all":
            return 1
        return 0


@dataclass
class ObwodRules:
    obwod: int
    typ_obszaru: str
    raw: str
    locality: str | None = None
    district: str | None = None
    streets: list[StreetRule] = field(default_factory=list)
    villages: list[str] = field(default_factory=list)

    def matches(self, street: str | None, number: int | None, village: str | None = None) -> bool:
        return self.match_specificity(street, number, village) is not None

    def match_specificity(
        self, street: str | None, number: int | None, village: str | None = None
    ) -> int | None:
        """Zwraca specyficzność najlepiej dopasowanej reguły, albo None jeśli brak dopasowania."""
        if self.villages and village:
            if any(villages_equal(v, village) for v in self.villages):
                return 3
            return None
        if self.streets and street:
            specs = [rule.specificity() for rule in self.streets if rule.matches(street, number)]
            return max(specs) if specs else None
        return None


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("ul.", "").replace("ul ", "").replace("al.", "").replace("al ", "")
    value = value.replace("os.", "").replace("pl.", "").replace("rondo ", "")
    value = re.sub(r"[\"'„”]", "", value)
    return value.strip(" ,.")


def streets_equal(a: str, b: str) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    if na == nb:
        return True
    # Dopasowanie po całych słowach (nie po dowolnym podciągu znaków) — luźny substring
    # dawał masowe fałszywe trafienia w gęstej sieci ulic Krakowa (np. "Kolejowa" w
    # "Nowa Kolejowa" ma inny numer administracyjny).
    ta, tb = na.split(), nb.split()
    if not ta or not tb:
        return False
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    n = len(shorter)
    return any(longer[i : i + n] == shorter for i in range(len(longer) - n + 1))


def villages_equal(a: str, b: str) -> bool:
    return normalize_text(a) == normalize_text(b)


def split_segments(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            segment = "".join(current).strip()
            if segment:
                parts.append(segment)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_range_fragment(fragment: str) -> tuple[list[NumberRange], Parity, str | None]:
    parity: Parity = "all"
    ranges: list[NumberRange] = []
    street_name: str | None = None

    parity_match = re.search(r"\((parzyste|nieparzyste)\)", fragment, re.I)
    if parity_match:
        parity = "even" if "parzyst" in parity_match.group(1).lower() else "odd"
        fragment = fragment[: parity_match.start()].strip()

    if re.search(r"\bdo ko[nń]ca\b", fragment, re.I):
        nums = re.findall(r"\d+", fragment)
        if nums:
            ranges.append(NumberRange(start=int(nums[-1]), end=None))
        fragment = re.split(r"\bdo ko[nń]ca\b", fragment, flags=re.I)[0].strip(" ,")

    for match in re.finditer(r"(\d+)\s*[-–—]\s*(\d+[a-z]?)", fragment, re.I):
        start = int(re.sub(r"\D", "", match.group(1)))
        end = int(re.sub(r"\D", "", match.group(2)))
        ranges.append(NumberRange(start=min(start, end), end=max(start, end)))
        fragment = fragment.replace(match.group(0), " ")

    for match in re.finditer(r"od\s+(\d+)\s+do\s+(\d+[a-z]?)", fragment, re.I):
        start = int(re.sub(r"\D", "", match.group(1)))
        end = int(re.sub(r"\D", "", match.group(2)))
        ranges.append(NumberRange(start=min(start, end), end=max(start, end)))
        fragment = fragment.replace(match.group(0), " ")

    fragment = re.sub(r"\s+", " ", fragment).strip(" ,.")
    if fragment:
        street_name = fragment
    return ranges, parity, street_name


def parse_city_description(body: str) -> tuple[str | None, str | None, list[StreetRule]]:
    district = None
    locality = None
    if ":" in body:
        prefix, rest = body.split(":", 1)
        prefix = prefix.strip()
        if "-" in prefix:
            locality, district = prefix.split("-", 1)
            locality = locality.strip()
            district = district.strip()
        else:
            locality = prefix.replace("ulice", "").strip()
        body = rest

    streets: list[StreetRule] = []
    pending_street: StreetRule | None = None

    for segment in split_segments(body):
        ranges, parity, street_name = parse_range_fragment(segment)
        if street_name and not ranges and parity == "all":
            pending_street = StreetRule(name=street_name)
            streets.append(pending_street)
            continue

        if street_name:
            streets.append(StreetRule(name=street_name, parity=parity, ranges=ranges))
            pending_street = streets[-1]
        elif ranges and pending_street is not None:
            pending_street.ranges.extend(ranges)
            if parity != "all":
                pending_street.parity = parity
        elif segment.strip():
            streets.append(StreetRule(name=segment.strip()))

    return locality, district, streets


def parse_opis_granic(obwod: int, typ_obszaru: str, raw: str) -> ObwodRules:
    text = raw.strip()
    rules = ObwodRules(obwod=obwod, typ_obszaru=typ_obszaru, raw=text)

    if typ_obszaru == "wieś" or (typ_obszaru == "miasto i wieś" and "ulice" not in text.lower()):
        villages = [part.strip() for part in re.split(r",| i ", text) if part.strip()]
        rules.villages = villages
        return rules

    if "ulice:" in text.lower() or "-" in text.split(":")[0]:
        locality, district, streets = parse_city_description(text)
        rules.locality = locality
        rules.district = district
        rules.streets = streets
        return rules

    if typ_obszaru == "miasto":
        rules.streets = [StreetRule(name=part.strip()) for part in split_segments(text) if part.strip()]
        return rules

    rules.villages = [text]
    return rules


def resolve_obwod(
    rules_list: list[ObwodRules],
    street: str | None,
    number: int | None,
    village: str | None = None,
) -> tuple[int | None, int]:
    """Zwraca (obwod albo None, liczba surowych dopasowań przed rozstrzygnięciem konfliktu).

    Gdy adres pasuje do wielu obwodów, wygrywa ten z najbardziej specyficzną regułą
    (zakres numerów > parzystość > cała ulica / miejscowość). Jeśli po tym kryterium
    nadal jest remis, zwracane jest None (prawdziwa niejednoznaczność).
    """
    scored = [
        (rules.obwod, rules.match_specificity(street, number, village)) for rules in rules_list
    ]
    scored = [(obwod, spec) for obwod, spec in scored if spec is not None]
    if not scored:
        return None, 0
    max_spec = max(spec for _, spec in scored)
    top = [obwod for obwod, spec in scored if spec == max_spec]
    winner = top[0] if len(top) == 1 else None
    return winner, len(scored)


def assign_obwod(
    rules_list: list[ObwodRules],
    street: str | None,
    number: int | None,
    village: str | None = None,
) -> int | None:
    winner, _ = resolve_obwod(rules_list, street, number, village)
    return winner
