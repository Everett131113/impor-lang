import json
with open("case.json", "r", encoding="utf-8") as f:
    data = json.load(f)
print("sum =", sum(data["values"]))
