import random
rows=100000
regions=["华东","华北","华南","西南","东北"]
cats=["硬件","软件","服务","培训"]
random.seed(20261119)
with open("apps/impor_sqlite_report/data/sales_100k.csv","w",encoding="utf-8",newline="\n") as f:
    f.write("region,category,amount\n")
    for i in range(rows):
        r=regions[i%5]
        c=cats[(i//5)%4]
        a=round(random.uniform(10, 2000),2)
        f.write(f"{r},{c},{a:.2f}\n")
print("rows",rows)
