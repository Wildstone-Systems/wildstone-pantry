"""Planning-owned review persistence and supported transaction boundary."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.recipe import Recipe


def worker(db):
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.execute(text("BEGIN IMMEDIATE"))
    return Session(bind=connection, join_transaction_mode="create_savepoint")


def attach(db, recipe_id, receipt):
    row = db.get(Recipe, recipe_id)
    row.import_review = receipt
    db.commit()
    return row
