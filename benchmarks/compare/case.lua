if os.getenv("IMPOR_COMPARE_FLOOR") then os.exit(0) end

local f = assert(io.open("case.json", "rb"))
local s = f:read("*a")
f:close()
local total = 0
for n in s:gmatch("%d+") do
  total = total + tonumber(n)
end
print("sum = " .. total)
