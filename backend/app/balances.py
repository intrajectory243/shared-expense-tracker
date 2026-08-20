"""Balance calculation (roadmap Phase 4).

Each expense splits its amount only among its own tagged participants,
weighted by each participant's share (a plain weight, not a dollar amount --
share=2 means twice the weight of share=1). Equal split is just everyone at
share=1. Net position per user = total they paid - total of their own
shares, adjusted by any settlements already logged. Positive net = owed to
them; negative = they owe the household.

For 3+ people, a raw pairwise "who owes who" table isn't well-defined, so
settlements_to_make is produced with a greedy min-cash-flow simplification:
repeatedly match the biggest creditor with the biggest debtor until every
net balance is zeroed out, minimizing the number of payments needed.
"""

from collections import defaultdict

from sqlalchemy.orm import Session, selectinload

from app.models import BalanceCache, Expense, Settlement, User
from app.schemas import BalanceEntry, BalanceSummary, DebtEntry

EPSILON = 0.005  # sub-cent noise from float division; ignore balances this small


def compute_net_balances(hh_db: Session, household_id: int) -> dict[int, float]:
    """Only touches Expense/ExpenseParticipant/Settlement -- all three live
    in the household file, so this needs just the one (household-scoped)
    session, unlike get_balance_summary below which also needs User names."""
    net: dict[int, float] = defaultdict(float)

    # participant_shares is lazy by default -- without eager-loading it here,
    # touching it per expense below turns this into one query per expense
    # (an N+1 that's invisible at a handful of expenses but costs seconds of
    # round-trip overhead once a household has hundreds+).
    expenses = (
        hh_db.query(Expense)
        .filter(Expense.household_id == household_id)
        .options(selectinload(Expense.participant_shares))
        .all()
    )
    for expense in expenses:
        shares = expense.participant_shares
        total_weight = sum(ps.share for ps in shares)
        if total_weight <= 0:
            continue
        net[expense.payer_id] += expense.amount
        for ps in shares:
            net[ps.user_id] -= expense.amount * (ps.share / total_weight)

    settlements = hh_db.query(Settlement).filter(Settlement.household_id == household_id).all()
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


def get_balance_summary(hh_db: Session, db: Session, household_id: int) -> BalanceSummary:
    """The template for stitching household-file data with shared-file
    User names -- every other per-household route that needs a name
    alongside an expense/settlement follows this same two-query,
    combine-in-Python pattern (see app/routers/expenses.py)."""
    net = compute_net_balances(hh_db, household_id)
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


def get_cached_balance_summary(hh_db: Session, db: Session, household_id: int) -> BalanceSummary:
    """Same result as get_balance_summary, but served from balance_cache when
    present. A cache row only ever holds the output of a real get_balance_summary
    call -- there's no separate update path that could drift from it, so a hit
    is always exactly what a fresh computation would have returned."""
    cached = hh_db.get(BalanceCache, household_id)
    if cached is not None:
        return BalanceSummary.model_validate_json(cached.payload)

    summary = get_balance_summary(hh_db, db, household_id)
    hh_db.merge(BalanceCache(household_id=household_id, payload=summary.model_dump_json()))
    hh_db.commit()
    return summary


def invalidate_balance_cache(hh_db: Session, household_id: int) -> None:
    """Call before committing any write that could change a household's balance
    (new/edited/deleted expense, new settlement). Deliberately doesn't commit --
    it rides along in the caller's own transaction so the invalidation can never
    succeed or fail independently of the write that made it necessary."""
    hh_db.query(BalanceCache).filter(BalanceCache.household_id == household_id).delete()
