import re

def _get_bids(game_log, player_name):
    matches = re.findall(rf"{player_name} bids ([\-0-9]+)", game_log)
    if matches == []:
        return 0
    return sum(map(int, matches)) / len(matches)


def _get_bet_evs(game_log, player_name, street_name):
    matches = re.search(rf"{player_name} {street_name} bets EV: ([\-0-9]+)", game_log)
    if matches is None:
        return 0
    return int(matches.group(1))

def _get_bounty_hits(game_log, player_name):
    matches = re.findall(rf"{player_name} hits their bounty", game_log)
    return len(matches)

def arbitrary_tournament_data_collection_function(gamelog):
    # This function collects data on games that are played in a tournament.
    # Gamelogs from tournaments are not saved since they can result in 200+GB of data, per tournament.
    # Parse the interesting data you want from the gamelog and return it here (but keep it small!)
    pnls_B = []
    pnls_A = []
    i = 0

    log_split_into_rounds = gamelog.split("Round #")
    # count number of times a team wins despite
    # the other player winning the auction
    A_win_auction_loss = 0
    B_win_auction_loss = 0
    for curr_round_string in log_split_into_rounds:
        B_wins_auction = "B won the auction" in curr_round_string
        A_wins_auction = "A won the auction" in curr_round_string
        if A_wins_auction and B_wins_auction:
            continue
        elif A_wins_auction and ("A awarded -" in curr_round_string):
            B_win_auction_loss += 1
        elif B_wins_auction and ("B awarded -" in curr_round_string):
            A_win_auction_loss += 1

    for n in range(100, 1100, 100):
        i = gamelog.find("Round #" + str(n), i)
        if i == -1:
            pnls_A.append("nan")
            pnls_B.append("nan")
            i = 0
        else:
            b_start = gamelog.find(
                "(", i
            )  # team B is listed first on even number rounds (i.e. multiples of 100)
            b_end = gamelog.find(")", i)
            a_start = gamelog.find("(", b_end + 1)
            a_end = gamelog.find(")", b_end + 1)

            if b_start != -1 and b_end != -1:
                pnls_B.append(gamelog[b_start + 1 : b_end])
            else:
                pnls_B.append("nan")

            if a_start != -1 and a_end != -1:
                pnls_A.append(gamelog[a_start + 1 : a_end])
            else:
                pnls_A.append("nan")

    return {
        "Aai": gamelog.count("A went all in"),
        "Bai": gamelog.count("B went all in"),
        "Ar": gamelog.count("A raises"),
        "Br": gamelog.count("B raises"),
        "Ab": gamelog.count("A bets"),
        "Bb": gamelog.count("B bets"),
        "Aca": gamelog.count("A calls"),
        "Bca": gamelog.count("B calls"),
        "Ach": gamelog.count("A checks"),
        "Bch": gamelog.count("B checks"),
        "Af": gamelog.count("A folds"),
        "Bf": gamelog.count("B folds"),
        "Ash": gamelog.count("A shows"),
        "Bsh": gamelog.count("B shows"),
        "pnls_A": pnls_A,
        "pnls_B": pnls_B,
        "bid_A": _get_bids(gamelog, "A"),
        "bid_B": _get_bids(gamelog, "B"),
        "bounty_hits_A": _get_bounty_hits(gamelog, "A"),
        "bounty_hits_B": _get_bounty_hits(gamelog, "B"),
        "A_bid_W": gamelog.count("A won the auction"),
        "B_bid_W": gamelog.count("B won the auction"),
        "A_W_bid_L": A_win_auction_loss,
        "B_W_bid_L": B_win_auction_loss,
        "ev_A_flop": _get_bet_evs(gamelog, "A", "flop"),
        "ev_B_flop": _get_bet_evs(gamelog, "B", "flop"),
        "ev_A_turn": _get_bet_evs(gamelog, "A", "turn"),
        "ev_B_turn": _get_bet_evs(gamelog, "B", "turn"),
    }

def main():
    # Read the gamelog file into a string
    with open('gamelog.txt', 'r') as file:
        gamelog_string = file.read()
    
    # Process the gamelog data and get the output
    result = arbitrary_tournament_data_collection_function(gamelog_string)
    
    # Print the result
    print(result)


if __name__ == "__main__":
    main()