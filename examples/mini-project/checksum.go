package example

// ByteSum is an intentionally simple checksum used only as a search fixture.
func ByteSum(data []byte) uint32 {
    var total uint32
    for _, value := range data {
        total += uint32(value)
    }
    return total
}
