'''
Encapsulates game and round state information for the player.
'''
from collections import namedtuple
from .actions import FoldAction, CallAction, CheckAction, RaiseAction, DiscardAction

GameState = namedtuple('GameState', ['bankroll', 'game_clock', 'round_num'])
TerminalState = namedtuple('TerminalState', ['deltas', 'previous_state'])

NUM_ROUNDS = 1000
STARTING_STACK = 400
BIG_BLIND = 2
SMALL_BLIND = 1


class RoundState(namedtuple('_RoundState', ['button', 'street', 'pips', 'stacks', 'hands', 'board', 'previous_state'])):
    '''
    Encodes the game tree for one round of poker.
    '''
    def showdown(self):
        '''
        Compares the players' hands and computes payoffs.
        '''
        return TerminalState([0, 0], self)

    def legal_actions(self):
        '''
        Returns a set which corresponds to the active player's legal moves.
        '''
        active = self.button % 2
        continue_cost = self.pips[1-active] - self.pips[active]
        if self.street in (2, 3):
            return {DiscardAction} if active != self.street % 2 else {CheckAction}
        if continue_cost == 0:
            # we can only raise the stakes if both players can afford it
            bets_forbidden = (self.stacks[0] == 0 or self.stacks[1] == 0)
            return {CheckAction, FoldAction} if bets_forbidden else {CheckAction, RaiseAction, FoldAction}
        # continue_cost > 0
        # similarly, re-raising is only allowed if both players can afford it
        raises_forbidden = (continue_cost == self.stacks[active] or self.stacks[1-active] == 0)
        return {FoldAction, CallAction} if raises_forbidden else {FoldAction, CallAction, RaiseAction}

    def raise_bounds(self):
        '''
        Returns a tuple of the minimum and maximum legal raises.
        '''
        active = self.button % 2
        continue_cost = self.pips[1-active] - self.pips[active]
        max_contribution = min(self.stacks[active], self.stacks[1-active] + continue_cost)
        min_contribution = min(max_contribution, continue_cost + max(continue_cost, BIG_BLIND))
        return (self.pips[active] + min_contribution, self.pips[active] + max_contribution)

    def proceed_street(self):
        '''
        Resets the players' pips and advances the game tree to the next round of betting and updates the board state.

        possible streets: 0, 2, 3, 4, 5, 6
        '''
        ### Put the board as peek deck of the street number and make sure board includes this peek + the players discarded cards after state
        if self.street == 6:
            return self.showdown()
        elif self.street == 0:
            new_street = 2
            button = 1 ### Player B discards first, since they are out of position
        elif self.street == 2:
            new_street = 3
            button = 0 ### Player A discards second
        else:
            new_street = self.street + 1
            button = 1 ### Player B acts first after the discard phase

        return RoundState(button, new_street, [0, 0], self.stacks, self.hands, self.board, self)


    def proceed(self, action):
        '''
        Advances the game tree by one action performed by the active player.

        Args:
            action: The action being performed. Must be one of:
                - DiscardAction: Player discards a card from their hand and adds it to the board
                - FoldAction: Player forfeits the hand
                - CallAction: Player matches the current bet
                - CheckAction: Player passes when no bet to match
                - RaiseAction: Player increases the current bet

        Returns:
            Either:
            - RoundState: The new state after the action is performed
            - TerminalState: If the action ends the hand (e.g., fold or final call)

        Note:
            The button value is incremented after each action to track whose turn it is.
            For DiscardAction, the card is added to the board and the hand is updated. Also, advances to the next street.
            For FoldAction, the inactive player is awarded the pot.
            For CallAction on button 0, both players post blinds.
            For CheckAction, advances to next street if both players have acted.
            For RaiseAction, updates pips and stacks based on raise amount.
        '''
        active = self.button % 2
        if isinstance(action, DiscardAction):
            if len(self.hands[active]) != 0:
                self.board.append(self.hands[active].pop(action.card))
            state = RoundState((1 - active) % 2, self.street, self.pips, self.stacks, self.hands, self.board, self)
            return state
        if isinstance(action, FoldAction):
            delta = self.stacks[0] - STARTING_STACK if active == 0 else STARTING_STACK - self.stacks[1]
            return TerminalState([delta, -delta], self)
        if isinstance(action, CallAction):
            if self.button == 0:  # sb calls bb
                return RoundState(1, 0, [BIG_BLIND] * 2, [STARTING_STACK - BIG_BLIND] * 2, self.hands, self.board,self)
            # both players acted
            new_pips = list(self.pips)
            new_stacks = list(self.stacks)
            contribution = new_pips[1-active] - new_pips[active]
            new_stacks[active] -= contribution
            new_pips[active] += contribution
            state = RoundState(self.button + 1, self.street, new_pips, new_stacks, self.hands, self.board, self)
            return state.proceed_street()
        if isinstance(action, CheckAction):
            if (self.street == 0 and self.button > 0) or self.button > 1 or self.street == 2 or self.street == 3:  # both players acted
                return self.proceed_street()
            # let opponent act
            return RoundState(self.button + 1, self.street, self.pips, self.stacks, self.hands, self.board, self)
        # isinstance(action, RaiseAction)
        new_pips = list(self.pips)
        new_stacks = list(self.stacks)
        contribution = action.amount - new_pips[active]
        new_stacks[active] -= contribution
        new_pips[active] += contribution
        return RoundState(self.button + 1, self.street, new_pips, new_stacks, self.hands, self.board, self)