'''
6.9630 MIT POKERBOTS GAME ENGINE
DO NOT REMOVE, RENAME, OR EDIT THIS FILE
'''
from collections import namedtuple
from threading import Thread
from queue import Queue
import time
import math
import json
import subprocess
import socket
import pkrbot ###import eval7, but better
import sys
import os
import random

sys.path.append(os.getcwd())
from config import *
###New action for discarding a card from your hand and adding it to the board
DiscardAction = namedtuple('DiscardAction', ['card'])

FoldAction = namedtuple('FoldAction', [])
CallAction = namedtuple('CallAction', [])
CheckAction = namedtuple('CheckAction', [])
# we coalesce BetAction and RaiseAction for convenience
RaiseAction = namedtuple('RaiseAction', ['amount'])

TerminalState = namedtuple('TerminalState', ['deltas', 'previous_state'])

STREET_NAMES = ['Flop', 'Discard 1', 'Discard 2', 'Turn', 'River']
DECODE = {'F': FoldAction, 'C': CallAction, 'K': CheckAction, 'R': RaiseAction, 'D': DiscardAction}
CCARDS = lambda cards: ','.join(map(str, cards))
PCARDS = lambda cards: '[{}]'.format(' '.join(map(str, cards))) ### Changed from PCARDS = lambda cards: '[{}]'.format(' '.join(map(str, cards)))
PVALUE = lambda name, value: ', {} ({})'.format(name, value)
STATUS = lambda players: ''.join([PVALUE(p.name, p.bankroll) for p in players])

# Socket encoding scheme:
#
# T#.### the player's game clock
# P# the- player's index
# H**,**,** the player's hand in common format
# F a fold action in the round history
# C a call action in the round history
# K a check action in the round history
# R### a raise action in the round history
# D# a discard action in the round history
# B**,**,**,**,**,** the board cards in common format
# O**,**,** the opponent's hand in common format
# A### the player's bankroll delta from the round
# Q game over
#
# Clauses are separated by spaces
# Messages end with '\n'
# The engine expects a response of K at the end of the round as an ack,
# otherwise a response which encodes the player's action
# Action history is sent once, including the player's actions


class RoundState(namedtuple('_RoundState', ['button', 'street', 'pips', 'stacks', 'hands', 'deck', 'board', 'previous_state'])):
    '''
    Encodes the game tree for one round of poker.
    '''

    def get_delta(self, winner_index: int) -> int:
        '''Returns the delta for player A and -delta for player B.

        Args:
            winner_index (int): Index of the winning player. Must be 0 (player A),
                1 (player B), or 2 (split pot).

        Returns:
            int: The delta value for player A and -delta for player B.
        '''
        assert winner_index in [0, 1, 2]
        delta = 0
        if winner_index == 2:
            # Case of split pots
            assert(self.stacks[0] == self.stacks[1]) # split pots only happen on the river + equal stacks
            delta = 0
        else:
            # Case of one player winning
            if winner_index == 0:
                delta = STARTING_STACK - self.stacks[1]
            else:
                delta = self.stacks[0] - STARTING_STACK

        # if delta is not an integer, round it down or up depending on who's in position
        if abs(delta - math.floor(delta)) > 1e-6:
            delta = math.floor(delta) if self.button % 2 == 0 else math.ceil(delta)
        return int(delta)

    def showdown(self) -> TerminalState:
        '''
        Compares the players' hands and computes the final payoffs at showdown.

        Evaluates both players' hands (hole cards + community cards) and determines
        the winner. The payoff (delta) is calculated based on:
        - The winner of the hand
        - The current pot size

        Returns:
            TerminalState: A terminal state object containing:
                - List of deltas (positive for winner, negative for loser)
                - Reference to the previous game state
        
        Note:
            This method assumes both players have equal stacks when reaching showdown,
            which is enforced by an assertion.
        '''
        score0 = pkrbot.evaluate(self.board + self.hands[0])
        score1 = pkrbot.evaluate(self.board + self.hands[1])
        assert(self.stacks[0] == self.stacks[1])
        if score0 > score1:
            delta = self.get_delta(0)
        elif score0 < score1:
            delta = self.get_delta(1)
        else:
            # split the pot
            delta = self.get_delta(2)
        
        return TerminalState([int(delta), -int(delta)], self)

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
            self.board.extend(self.deck.peek(new_street))
        elif self.street == 2:
            new_street = 3
            button = 0 ### Player A discards second
        elif self.street == 3:
            new_street = 4
            button = 1 ### Player B acts first after the discard phase
        else:
            new_street = self.street + 1
            button = 1
            self.board.append(self.deck.peek(new_street - 1)[new_street - 2])

        return RoundState(button, new_street, [0, 0], self.stacks, self.hands, self.deck, self.board, self)

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
            state = RoundState((1 - active) % 2, self.street, self.pips, self.stacks, self.hands, self.deck, self.board, self)
            return state
        if isinstance(action, FoldAction):
            delta = self.get_delta((1 - active) % 2) # if active folds, the other player (1 - active) wins
            return TerminalState([delta, -delta], self)
        if isinstance(action, CallAction):
            if self.button == 0:  # sb calls bb
                return RoundState(1, 0, [BIG_BLIND] * 2, [STARTING_STACK - BIG_BLIND] * 2, self.hands, self.deck, self.board,self)
            # both players acted
            new_pips = list(self.pips)
            new_stacks = list(self.stacks)
            contribution = new_pips[1-active] - new_pips[active]
            new_stacks[active] -= contribution
            new_pips[active] += contribution
            state = RoundState(self.button + 1, self.street, new_pips, new_stacks, self.hands, self.deck, self.board, self)
            return state.proceed_street()
        if isinstance(action, CheckAction):
            if (self.street == 0 and self.button > 0) or self.button > 1 or self.street == 2 or self.street == 3:  # both players acted
                return self.proceed_street()
            # let opponent act
            return RoundState(self.button + 1, self.street, self.pips, self.stacks, self.hands, self.deck, self.board, self)
        # isinstance(action, RaiseAction)
        new_pips = list(self.pips)
        new_stacks = list(self.stacks)
        contribution = action.amount - new_pips[active]
        new_stacks[active] -= contribution
        new_pips[active] += contribution
        return RoundState(self.button + 1, self.street, new_pips, new_stacks, self.hands, self.deck, self.board, self)


class Player():
    '''
    Handles subprocess and socket interactions with one player's pokerbot.
    '''

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.game_clock = STARTING_GAME_CLOCK
        self.bankroll = 0
        self.commands = None
        self.bot_subprocess = None
        self.socketfile = None
        self.bytes_queue = Queue()

    def build(self):
        '''
        Loads the commands file and builds the pokerbot.
        '''
        try:
            with open(self.path + '/commands.json', 'r') as json_file:
                commands = json.load(json_file)
            if ('build' in commands and 'run' in commands and
                    isinstance(commands['build'], list) and
                    isinstance(commands['run'], list)):
                self.commands = commands
            else:
                print(self.name, 'commands.json missing command')
        except FileNotFoundError:
            print(self.name, 'commands.json not found - check PLAYER_PATH')
        except json.decoder.JSONDecodeError:
            print(self.name, 'commands.json misformatted')
        if self.commands is not None and len(self.commands['build']) > 0:
            try:
                proc = subprocess.run(self.commands['build'],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      cwd=self.path, timeout=BUILD_TIMEOUT, check=False)
                self.bytes_queue.put(proc.stdout)
            except subprocess.TimeoutExpired as timeout_expired:
                error_message = 'Timed out waiting for ' + self.name + ' to build'
                print(error_message)
                self.bytes_queue.put(timeout_expired.stdout)
                self.bytes_queue.put(error_message.encode())
            except (TypeError, ValueError):
                print(self.name, 'build command misformatted')
            except OSError:
                print(self.name, 'build failed - check "build" in commands.json')

    def run(self):
        '''
        Runs the pokerbot and establishes the socket connection.
        '''
        if self.commands is not None and len(self.commands['run']) > 0:
            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                with server_socket:
                    server_socket.bind(('', 0))
                    server_socket.settimeout(CONNECT_TIMEOUT)
                    server_socket.listen()
                    port = server_socket.getsockname()[1]
                    proc = subprocess.Popen(self.commands['run'] + [str(port)],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            cwd=self.path)
                    self.bot_subprocess = proc
                    # function for bot listening
                    def enqueue_output(out, queue):
                        try:
                            for line in out:
                                if self.path == r"./player_chatbot":
                                    print(line.strip().decode("utf-8"))
                                else:
                                    queue.put(line)
                        except ValueError:
                            pass
                    # start a separate bot listening thread which dies with the program
                    Thread(target=enqueue_output, args=(proc.stdout, self.bytes_queue), daemon=True).start()
                    # block until we timeout or the player connects
                    client_socket, _ = server_socket.accept()
                    with client_socket:
                        if self.path == r"./player_chatbot":
                            client_socket.settimeout(PLAYER_TIMEOUT)
                        else:
                            client_socket.settimeout(CONNECT_TIMEOUT)
                        sock = client_socket.makefile('rw')
                        self.socketfile = sock
                        print(self.name, 'connected successfully')
            except (TypeError, ValueError):
                print(self.name, 'run command misformatted')
            except OSError:
                print(self.name, 'run failed - check "run" in commands.json')
            except socket.timeout:
                print('Timed out waiting for', self.name, 'to connect')

    def stop(self):
        '''
        Closes the socket connection and stops the pokerbot.
        '''
        if self.socketfile is not None:
            try:
                self.socketfile.write('Q\n')
                self.socketfile.close()
            except socket.timeout:
                print('Timed out waiting for', self.name, 'to disconnect')
            except OSError:
                print('Could not close socket connection with', self.name)
        if self.bot_subprocess is not None:
            try:
                if self.path == r"./player_chatbot":
                    outs, _ = self.bot_subprocess.communicate(timeout=PLAYER_TIMEOUT)
                else:
                    outs, _ = self.bot_subprocess.communicate(timeout=CONNECT_TIMEOUT)
                self.bytes_queue.put(outs)
            except subprocess.TimeoutExpired:
                print('Timed out waiting for', self.name, 'to quit')
                self.bot_subprocess.kill()
                outs, _ = self.bot_subprocess.communicate()
                self.bytes_queue.put(outs)
        with open(self.name + '.txt', 'wb') as log_file:
            bytes_written = 0
            for output in self.bytes_queue.queue:
                try:
                    bytes_written += log_file.write(output)
                    if bytes_written >= PLAYER_LOG_SIZE_LIMIT:
                        break
                except TypeError:
                    pass

    def query(self, round_state, player_message, game_log):
        '''
        Requests one action from the pokerbot over the socket connection.

        This method handles communication with the bot, sending the current game state
        and receiving the bot's chosen action. It enforces game clock constraints and
        validates that the received action is legal.

        Args:
            round_state (RoundState or TerminalState): The current state of the game.
            player_message (list): Messages to be sent to the player bot, including game state
                information like time remaining, player position, and cards.
            game_log (list): A list to store game events and error messages.

        Returns:
            Action: One of DiscardAction, FoldAction, CallAction, CheckAction, or RaiseAction representing
            the bot's chosen action. If the bot fails to provide a valid action, returns:
                - CheckAction if it's a legal move
                - FoldAction if check is not legal

        Notes:
            - The game clock is decremented by the time taken to receive a response
            - Invalid or illegal actions are logged but not executed
            - Bot disconnections or timeouts result in game clock being set to 0
            - At the end of a round, only CheckAction is considered legal
        '''
        legal_actions = round_state.legal_actions() if isinstance(round_state, RoundState) else {CheckAction}
        if self.socketfile is not None and self.game_clock > 0.:
            clause = ''
            try:
                player_message[0] = 'T{:.3f}'.format(self.game_clock)
                message = ' '.join(player_message) + '\n'
                del player_message[1:]  # do not send redundant action history
                start_time = time.perf_counter()
                self.socketfile.write(message)
                self.socketfile.flush()
                clause = self.socketfile.readline().strip()
                end_time = time.perf_counter()
                if ENFORCE_GAME_CLOCK and self.path != r"./player_chatbot":
                    self.game_clock -= end_time - start_time
                if self.game_clock <= 0.:
                    raise socket.timeout
                action = DECODE[clause[0]]
                if action in legal_actions:
                    if clause[0] == 'R':
                        amount = int(clause[1:])
                        min_raise, max_raise = round_state.raise_bounds()
                        if min_raise <= amount <= max_raise:
                            return action(amount)
                    elif clause[0] == 'D':
                        card = int(clause[1:])
                        if 0 <= card <= 2:
                            return action(card)
                        else:
                            game_log.append(f"{self.name} attempted to discard invalid index {card}")
                            # Invalid index - fall through to default action handling
                        ###### index the player's hand 'D0', 'D1', or 'D2' ######
                    else:
                        return action()
                else:
                    # Action is not in legal_actions
                    game_log.append(f"street = {round_state.street}")
                    game_log.append(self.name + ' attempted illegal ' + action.__name__)
            except socket.timeout:
                error_message = self.name + ' ran out of time'
                game_log.append(error_message)
                print(error_message)
                self.game_clock = 0.
            except OSError:
                error_message = self.name + ' disconnected'
                game_log.append(error_message)
                print(error_message)
                self.game_clock = 0.
            except (IndexError, KeyError, ValueError):
                game_log.append(self.name + ' response misformatted: ' + str(clause))
        return CheckAction() if CheckAction in legal_actions else FoldAction()


class Game():
    '''
    Manages logging and the high-level game procedure.
    '''

    def __init__(self):
        self.log = ['6.9630 MIT Pokerbots - ' + PLAYER_1_NAME + ' vs ' + PLAYER_2_NAME]
        self.player_messages = [[], []]
        self.preflop_bets = {PLAYER_1_NAME: 0, PLAYER_2_NAME: 0}
        self.flop_bets = {PLAYER_1_NAME: 0, PLAYER_2_NAME: 0}
        self.turn_bets = {PLAYER_1_NAME: 0, PLAYER_2_NAME: 0}
        self.ev_preflop_bets = {PLAYER_1_NAME: 0, PLAYER_2_NAME: 0}
        self.ev_flop_bets = {PLAYER_1_NAME: 0, PLAYER_2_NAME: 0}
        self.ev_turn_bets = {PLAYER_1_NAME: 0, PLAYER_2_NAME: 0}

    def log_round_state(self, players, round_state):
        '''
        Incorporates RoundState information into the game log and player messages.
        '''
        if round_state.street == 0:
            self.preflop_bets = {players[0].name: STARTING_STACK-round_state.stacks[0],
                                     players[1].name: STARTING_STACK-round_state.stacks[1]}
        elif round_state.street == 4:
            self.flop_bets = {players[0].name: STARTING_STACK-round_state.stacks[0]-self.preflop_bets[players[0].name],
                                players[1].name: STARTING_STACK-round_state.stacks[1]-self.preflop_bets[players[1].name]}
        else:
            self.turn_bets = {players[0].name: STARTING_STACK-round_state.stacks[0]-self.flop_bets[players[0].name]-self.preflop_bets[players[0].name],
                                players[1].name: STARTING_STACK-round_state.stacks[1]-self.flop_bets[players[1].name]-self.preflop_bets[players[1].name]}
            
        
        if round_state.street == 0 and round_state.button == 0:
            self.log.append('{} posts the blind of {}'.format(players[0].name, SMALL_BLIND))
            self.log.append('{} posts the blind of {}'.format(players[1].name, BIG_BLIND))
            self.log.append('{} dealt {}'.format(players[0].name, PCARDS(round_state.hands[0])))
            self.log.append('{} dealt {}'.format(players[1].name, PCARDS(round_state.hands[1])))
            self.player_messages[0] = ['T0.', 'P0', 'H' + CCARDS(round_state.hands[0]), 'G']
            self.player_messages[1] = ['T0.', 'P1', 'H' + CCARDS(round_state.hands[1]), 'G']
        elif (round_state.street > 0 and round_state.street != 3 and round_state.button == 1) or (round_state.street == 3 and round_state.button == 0):
            board = round_state.board
            self.log.append(STREET_NAMES[round_state.street - 2] + ' ' + PCARDS(board) +
                            PVALUE(players[0].name, STARTING_STACK-round_state.stacks[0]) +
                            PVALUE(players[1].name, STARTING_STACK-round_state.stacks[1]))
            self.log.append(f"Current stacks: {round_state.stacks[0]}, {round_state.stacks[1]}")
            compressed_board = 'B' + CCARDS(board)
            self.player_messages[0].append(compressed_board)
            self.player_messages[1].append(compressed_board)

    def log_action(self, name, action, bet_override, hand):
        '''
        Incorporates action information into the game log and player messages.
        '''
        if isinstance(action, FoldAction):
            phrasing = ' folds'
            code = 'F'
        elif isinstance(action, CallAction):
            phrasing = ' calls'
            code = 'C'
        elif isinstance(action, CheckAction):
            phrasing = ' checks'
            code = 'K'
        elif isinstance(action, DiscardAction):
            phrasing = ' discards ' + str(hand[action.card])
            code = 'D' + str(action.card)
        else:  # isinstance(action, RaiseAction)
            phrasing = (' bets ' if bet_override else ' raises to ') + str(action.amount)
            code = 'R' + str(action.amount)
        self.log.append(name + phrasing)
        self.player_messages[0].append(code)
        self.player_messages[1].append(code)

    def log_terminal_state(self, players, round_state):
        '''
        Incorporates TerminalState information into the game log and player messages.
        '''
        previous_state = round_state.previous_state
        if not self.log[-1].endswith(' folds'):
            self.log.append('{} shows {}'.format(players[0].name, PCARDS(previous_state.hands[0])))
            self.log.append('{} shows {}'.format(players[1].name, PCARDS(previous_state.hands[1])))
            self.player_messages[0].append('O' + CCARDS(previous_state.hands[1]))
            self.player_messages[1].append('O' + CCARDS(previous_state.hands[0]))
        self.log.append('{} awarded {}'.format(players[0].name, round_state.deltas[0]))
        self.log.append('{} awarded {}'.format(players[1].name, round_state.deltas[1]))
        self.player_messages[0].append('A' + str(round_state.deltas[0]))
        self.player_messages[1].append('A' + str(round_state.deltas[1]))

    def run_round(self, players):
        '''
        Runs one round of poker.
        '''
        deck = pkrbot.Deck()
        deck.shuffle()
        hands = [deck.deal(3), deck.deal(3)]
        board = []
        pips = [SMALL_BLIND, BIG_BLIND]
        stacks = [STARTING_STACK - SMALL_BLIND, STARTING_STACK - BIG_BLIND]
        round_state = RoundState(0, 0, pips, stacks, hands, deck, board, None)
        while not isinstance(round_state, TerminalState):
            self.log_round_state(players, round_state)
            active = round_state.button % 2
            player = players[active]
            action = player.query(round_state, self.player_messages[active], self.log)
            bet_override = (round_state.pips == [0, 0])
            self.log_action(player.name, action, bet_override, round_state.hands[active])
            round_state = round_state.proceed(action)
        self.log_terminal_state(players, round_state)
        for i in range(len(players)):
            multiplier = 1 if round_state.deltas[i] > 0 else (0 if round_state.deltas[i] == 0 else -1)
            self.ev_preflop_bets[players[i].name] += multiplier * self.preflop_bets[players[i].name]
            self.ev_flop_bets[players[i].name] += multiplier * self.flop_bets[players[i].name]
            self.ev_turn_bets[players[i].name] += multiplier * self.turn_bets[players[i].name]
        for player, player_message, delta in zip(players, self.player_messages, round_state.deltas):
            player.query(round_state, player_message, self.log)
            player.bankroll += delta

    def run(self):
        '''
        Runs one game of poker.
        '''
        print('   __  _____________  ___       __           __        __    ')
        print('  /  |/  /  _/_  __/ / _ \\___  / /_____ ____/ /  ___  / /____')
        print(' / /|_/ // /  / /   / ___/ _ \\/  \'_/ -_) __/ _ \\/ _ \\/ __(_-<')
        print('/_/  /_/___/ /_/   /_/   \\___/_/\\_\\\\__/_/ /_.__/\\___/\\__/___/')
        print()
        print('Starting the Pokerbots engine...')
        players = [
            Player(PLAYER_1_NAME, PLAYER_1_PATH),
            Player(PLAYER_2_NAME, PLAYER_2_PATH)
        ]

        for player in players:
            player.build()
        for player in players:
            player.run()
        for round_num in range(1, NUM_ROUNDS + 1):
            self.log.append('')
            self.log.append('Round #' + str(round_num) + STATUS(players))
            self.run_round(players)
            players = players[::-1]
            
        self.log.append('')
        self.log.append('Final' + STATUS(players))
        
        for player in players:
            self.log.append('{} preflop bets EV: {}'.format(player.name, self.ev_preflop_bets[player.name]))
            self.log.append('{} flop bets EV: {}'.format(player.name, self.ev_flop_bets[player.name]))
            self.log.append('{} turn bets EV: {}'.format(player.name, self.ev_turn_bets[player.name]))
            player.stop()
        name = GAME_LOG_FILENAME + '.txt'
        print('Writing', name)
        with open(name, 'w') as log_file:
            log_file.write('\n'.join(self.log))


if __name__ == '__main__':
    Game().run()