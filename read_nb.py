import json

with open('Erdem_Akay_Project_Code.ipynb', encoding='utf-8') as f:
    nb = json.load(f)

print(f"Total cells: {len(nb['cells'])}")
print()

for i, c in enumerate(nb['cells']):
    ct = c['cell_type']
    src = ''.join(c['source'])[:400]
    print(f"=== Cell {i} [{ct}] ===")
    print(src)
    print()
