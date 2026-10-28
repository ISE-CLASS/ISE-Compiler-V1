int main() {
    int a = 1, b = 2, c = 3, x, y;
    x = y = a + b * c;          // right-assoc =, * before +
    x = a - b - c;              // left-assoc: (a - b) - c
    y = (a + b) * c % 4;
    bool p = a < b == b < c;    // relational before equality
    bool q = a || b && !c;      // && before ||
    x += -a * ++b;
    y = x-- + c;
    std::cout << a + b << std::endl;
    return 0;
}
