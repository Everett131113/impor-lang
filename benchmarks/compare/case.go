package main

import (
	"encoding/json"
	"fmt"
	"os"
)

func main() {
	if os.Getenv("IMPOR_COMPARE_FLOOR") != "" {
		return
	}
	b, err := os.ReadFile("case.json")
	if err != nil {
		panic(err)
	}
	var data struct {
		Values []int `json:"values"`
	}
	if err := json.Unmarshal(b, &data); err != nil {
		panic(err)
	}
	total := 0
	for _, v := range data.Values {
		total += v
	}
	fmt.Println("sum =", total)
}
