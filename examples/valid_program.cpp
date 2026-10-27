#include <iostream>
#include <string>
using namespace std;

// Global declarations
int counter = 0;
double rates[3] = {1.5, 2.25, 3.0};

int square(int x);          // prototype

int square(int x) {
    return x * x;
}

void greet(std::string name) {
    cout << "Hello, " << name << "!" << endl;
}

int sum(int values[], int n) {
    int total = 0, i;
    for (i = 0; i < n; i++) {
        total += values[i];
    }
    return total;
}

int main() {
    int a = 5, b = 3;
    int nums[5] = {1, 2, 3, 4, 5};
    bool done = false;
    char grade = 'A';
    string word = "compiler";

    greet(word);
    cout << "square(a) = " << square(a) << endl;
    cout << "sum = " << sum(nums, 5) << endl;

    if (a > b && !done) {
        cout << "a is bigger" << endl;
    } else if (a == b) {
        cout << "equal" << endl;
    } else {
        cout << "b is bigger" << endl;
    }

    while (counter < 3) {
        counter++;
        if (counter == 2) continue;
    }

    do {
        a -= 1;
    } while (a > 0);

    for (int k = 0; k < 10; ++k) {
        if (k % 2 == 0) {
            { int nested = k * 2; }
        }
        if (k > 7) break;
    }

    cin >> b;
    return 0;
}
