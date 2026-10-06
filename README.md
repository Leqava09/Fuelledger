# FuelLedger

Standalone Django application for site-based fuel station cash-up and shift records. It does not import or modify Foodline Control.

The project targets Python 3.14 and Django 6.0.1 to match Foodline's active virtual environment. Foodline's requirements file currently pins Django 5.1.6, so its declared pin differs from its installed runtime.

FuelLedger uses PostgreSQL only. It defaults to a separate database named `fuelledger`; configure its connection with the `FUELLEDGER_DATABASE_*` settings in `.env` or the process environment. Copy `.env.example` to `.env` and provide the PostgreSQL connection values for local use and deployment. Do not set its database name to Foodline's `foodlinecontrol` database.

Create a virtual environment, install `requirements.txt`, set `FUELLEDGER_SECRET_KEY`, then run `python manage.py migrate`, `python manage.py createsuperuser`, and `python manage.py runserver`.

Use the Django admin to create sites and users. A user's FuelLedger permissions section assigns their HQ/site role and whether they can configure cash-up fields. Give site users a site assignment; HQ users have no single site assignment. New sites receive a starter cash-up form the first time their ledger is opened.