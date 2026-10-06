package javabot.skeleton;

/**
 * The actions that the player is allowed to take.
 */
public class Action {
    public ActionType actionType;
    public int amount;
    public int card;

    public Action(ActionType actionType) {
        this.actionType = actionType;
        this.amount = 0;
        this.card = 0;
    }

    public Action(ActionType actionType, int amount) {
        this.actionType = actionType;
        this.amount = amount;
        this.card = 0;
    }

    public Action(ActionType actionType, int amount, int card) {
        this.actionType = actionType;
        this.amount = amount;
        this.card = card;
    }
}
