#pragma once

#include <iostream>

#include "constants.h"

namespace pokerbots::skeleton {

  struct Action {
    enum Type { FOLD, CALL, CHECK, RAISE, DISCARD };

    Type actionType;
    int amount;
    int card;

    Action(Type t = Type::CHECK, int a = 0, int b = 0): actionType(t), amount(a), card(b) {}

    friend std::ostream& operator<<(std::ostream& os, const Action& a);
  };

} // namespace pokerbots::skeleton
