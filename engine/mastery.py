"""New-hire mastery: Beta posterior per rule, updated on unaided first attempts only."""


class Mastery:
    def __init__(self, rule_ids):
        self.ab = {r: [1.0, 1.0] for r in rule_ids}
        self.hints = {r: 0 for r in rule_ids}

    def update(self, rule_id: str, correct_first_try: bool, hinted: bool = False):
        if hinted:
            self.hints[rule_id] += 1
            return
        self.ab[rule_id][0 if correct_first_try else 1] += 1

    def mean(self, r): a, b = self.ab[r]; return a / (a + b)
    def var(self, r): a, b = self.ab[r]; return a * b / ((a + b) ** 2 * (a + b + 1))

    def next_scenario(self, scenarios: list[dict], risk: dict, done: set):
        """scenario = {id, rule_ids}. Pick max risk x posterior variance, skipping done ones."""
        best = None
        for s in scenarios:
            if s["id"] in done:
                continue
            score = sum(risk[r] * self.var(r) for r in s["rule_ids"]) / len(s["rule_ids"])
            if best is None or score > best[1]:
                best = (s, score)
        return (best[0], round(best[1], 4)) if best else (None, 0)

    def summary(self):
        rows = [{"rule_id": r, "mean": round(self.mean(r), 2), "var": round(self.var(r), 3), "hinted": self.hints[r],
                 "level": "mastered" if self.mean(r) >= 0.67 and sum(self.ab[r]) >= 3.5 else
                          "practice next" if self.mean(r) < 0.5 else "learning"} for r in self.ab]
        return rows
