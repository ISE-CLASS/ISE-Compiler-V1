#include <iostream>
using namespace std;

int square(int x) {
    return x * x;
}

int scale(int v) {
    return v * 8 + 0;                   // strength reduction + algebra
}

int main() {
    int width = 4, height = 5;
    int area = width * height;          // constant propagation + folding
    int unused = area + 100;            // never read -> removed
    int doubled = area * 2 + 0;         // algebraic simplification
    int scaled = width * 8;             // strength reduction
    bool debug = false;

    if (debug) {                        // constant condition
        cout << "debug mode" << endl;
    } else {
        cout << "area = " << area << endl;
    }

    while (false) {                     // never runs
        cout << "never" << endl;
    }

    cout << doubled << " " << scaled << " " << square(3 + 4) << " " << scale(2) << endl;
    return 0;
    cout << "unreachable" << endl;      // after return
}