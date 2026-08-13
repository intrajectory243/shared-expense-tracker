"""Balance calculation (roadmap Phase 4).

Each expense splits its amount only among its own tagged participants.
Net position per user = total they paid - total of their own shares,
adjusted by any settlements already logged. Positive net = owed to them;
negative = they owe the household.

For 3+ people, a raw pairwise "who owes who" table isn't well-defined, so
settlements_to_make is produced with a greedy min-cash-flow simplification:
repeatedly match the biggest creditor with the biggest debtor until every
net balance is zeroed out, minimizing the number of payments needed.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import Expense, Settlement, User
from app.schemas import BalanceEntry, BalanceSummary, DebtEntry

EPSILON = 0.005  # sub-cent noise from float division; ignore balances this small


def compute_net_balances(db: Session, household_id: int) -> dict[int, float]:
    net: dict[int, float] = defaultdict(float)

    expenses = db.query(Expense).filter(Expense.household_id == household_id).all()
    for expense in expenses:
        participants = expense.participants
        if not participants:
            continue
        share = expense.amount / len(participants)
        net[expense.payer_id] += expense.amount
        for participant in participants:
            net[participant.id] -= share

    settlements = db.query(Settlement).filter(Settlement.household_id == household_id).all()
    for settlement in settlements:
        net[settlement.from_user_id] += settlement.amount
        net[settlement.to_user_id] -= settlement.amount

    return net


def simplify_debts(net: dict[int, float]) -> list[tuple[int, int, float]]:
    """Greedy min-cash-flow: pair largest creditor with largest debtor each round."""
    creditors = [[uid, amt] for uid, amt in net.items() if amt > EPSILON]
    debtors = [[uid, -amt] for uid, amt in net.items() if amt < -EPSILON]
    creditors.sort(key=lambda x: x[1], reverse=True)
    debtors.sort(key=lambda x: x[1], reverse=True)

    transactions: list[tuple[int, int, float]] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        debtor_id, debt_amt = debtors[i]
        creditor_id, credit_amt = creditors[j]
        payment = round(min(debt_amt, credit_amt), 2)

        if payment > EPSILON:
            transactions.append((debtor_id, creditor_id, payment))

        debtors[i][1] -= payment
        creditors[j][1] -= payment
        if debtors[i][1] <= EPSILON:
            i += 1
        if creditors[j][1] <= EPSILON:
            j += 1

    return transactions


def get_balance_summary(db: Session, household_id: int) -> BalanceSummary:
    net = compute_net_balances(db, household_id)
    users = {u.id: u for u in db.query(User).filter(User.household_id == household_id).all()}

    balances = [
        BalanceEntry(user_id=uid, name=users[uid].name, net=round(amount, 2))
        for uid, amount in net.items()
        if uid in users and abs(amount) > EPSILON
    ]
    balances.sort(key=lambda b: b.net, reverse=True)

    debts = [
        DebtEntry(
            from_user_id=from_id,
            from_name=users[from_id].name,
            to_user_id=to_id,
            to_name=users[to_id].name,
            amount=amount,
        )
        for from_id, to_id, amount in simplify_debts(net)
        if from_id in users and to_id in users
    ]

    return BalanceSummary(balances=balances, settlements_to_make=debts)
