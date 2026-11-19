const fs = require("fs");
const data = JSON.parse(fs.readFileSync("case.json", "utf8"));
let total = 0;
for (const v of data.values) total += v;
console.log("sum =", total);
