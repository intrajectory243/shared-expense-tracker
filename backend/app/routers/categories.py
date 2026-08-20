"""Household-owned expense categories (roadmap Phase 9). Category lives in
the household file (app/models.py::Category), seeded lazily the first time
anyone reads the list for a household that doesn't have any yet -- same
"nothing until it's actually needed" approach the household file itself
already uses. Expense.category stays a plain string column, not a FK to
Category.id: a rename is a bulk UPDATE of matching Expense rows in the same
transaction, not a join everywhere a category name is displayed."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_current_active_user, get_household_db, require_household
from app.models import Category, Expense, Language, User
from app.schemas import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["categories"])

# Matches frontend/app.js's CATS/CAT_LABELS -- kept as plain strings, not a
# shared source of truth with the frontend, since these are only ever used
# once per household (the seed) and then become ordinary editable rows.
_DEFAULT_CATEGORIES = {
    Language.en: ["Rent", "Groceries", "Utilities", "Household", "Eating out", "Transport", "Other"],
    Language.fa: ["اجاره", "خواروبار", "آب و برق", "خانه", "رستوران", "حمل‌ونقل", "سایر"],
}


def _seed_if_empty(hh_db: Session, household_id: int, language: Language) -> None:
    if hh_db.query(Category).filter(Category.household_id == household_id).first() is not None:
        return
    defaults = _DEFAULT_CATEGORIES.get(language, _DEFAULT_CATEGORIES[Language.en])
    hh_db.add_all([Category(household_id=household_id, name=name) for name in defaults])
    hh_db.commit()


def _usage_by_name(hh_db: Session, household_id: int) -> dict[str, int]:
    rows = (
        hh_db.query(Expense.category, func.count(Expense.id))
        .filter(Expense.household_id == household_id)
        .group_by(Expense.category)
        .all()
    )
    return dict(rows)


@router.get("", response_model=list[CategoryOut])
def list_categories(
    user: User = Depends(require_household),
    hh_db: Session = Depends(get_household_db),
):
    _seed_if_empty(hh_db, user.household_id, user.language)
    categories = hh_db.query(Category).filter(Category.household_id == user.household_id).order_by(Category.id).all()
    usage = _usage_by_name(hh_db, user.household_id)
    for c in categories:
        c.usage = usage.get(c.name, 0)
    return categories


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    user: User = Depends(get_current_active_user),
    hh_db: Session = Depends(get_household_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name the category first.")
    existing = hh_db.query(Category).filter(Category.household_id == user.household_id).all()
    if any(c.name.lower() == name.lower() for c in existing):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That one already exists.")
    category = Category(household_id=user.household_id, name=name)
    hh_db.add(category)
    hh_db.commit()
    hh_db.refresh(category)
    category.usage = 0
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
def rename_category(
    category_id: int,
    payload: CategoryUpdate,
    user: User = Depends(get_current_active_user),
    hh_db: Session = Depends(get_household_db),
):
    category = (
        hh_db.query(Category)
        .filter(Category.id == category_id, Category.household_id == user.household_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name the category first.")
    others = (
        hh_db.query(Category)
        .filter(Category.household_id == user.household_id, Category.id != category_id)
        .all()
    )
    if any(c.name.lower() == new_name.lower() for c in others):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That one already exists.")

    old_name = category.name
    category.name = new_name
    if old_name != new_name:
        hh_db.query(Expense).filter(
            Expense.household_id == user.household_id, Expense.category == old_name
        ).update({"category": new_name}, synchronize_session=False)
    hh_db.commit()
    hh_db.refresh(category)
    category.usage = _usage_by_name(hh_db, user.household_id).get(new_name, 0)
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    user: User = Depends(get_current_active_user),
    hh_db: Session = Depends(get_household_db),
):
    category = (
        hh_db.query(Category)
        .filter(Category.id == category_id, Category.household_id == user.household_id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    usage = _usage_by_name(hh_db, user.household_id).get(category.name, 0)
    if usage > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{category.name} is still on {usage} expense{'s' if usage != 1 else ''}.",
        )
    remaining = hh_db.query(Category).filter(Category.household_id == user.household_id).count()
    if remaining <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keep at least one category.")

    hh_db.delete(category)
    hh_db.commit()
