# FuelLedger

Standalone Django application for site-based fuel station cash-up and shift records. It does not import or modify Foodline Control.

The cPanel deployment targets Python 3.11, Django 5.1.6, and PostgreSQL 13 to match the existing hosting environment. Django 5.1 is out of security support. PostgreSQL 13 is also end-of-life, and Django 5.2 requires PostgreSQL 14 or newer, so upgrade the hosted database before upgrading Django and treating this as a supported long-term production configuration.

FuelLedger uses PostgreSQL only. It defaults to a separate database named `fuelledger`; configure its connection with the `FUELLEDGER_DATABASE_*` settings in `.env` or the process environment. Copy `.env.example` to `.env` and provide the PostgreSQL connection values for local use and deployment. Do not set its database name to Foodline's `foodlinecontrol` database.

Create a virtual environment, install `requirements.txt`, set `FUELLEDGER_SECRET_KEY`, then run `python manage.py migrate`, `python manage.py createsuperuser`, and `python manage.py runserver`.

Use the Django admin to create sites and users. A user's FuelLedger permissions section assigns their HQ/site role and whether they can configure cash-up fields. Give site users a site assignment; HQ users have no single site assignment. New sites receive a starter cash-up form the first time their ledger is opened.

## cPanel deployment

FuelLedger uses PostgreSQL and the cPanel Python 3.11 virtual environment. Keep it separate from FoodlineControl: create a dedicated PostgreSQL database and database user in cPanel/phpPgAdmin, and keep the cPanel Python application root as `Fuelledger` with the matching domain or subdomain.

The Git repository and cPanel Passenger application root are both `~/Fuelledger`. Create `~/Fuelledger/.env` from `~/Fuelledger/.env.example` and set a private `FUELLEDGER_SECRET_KEY`, `FUELLEDGER_DEBUG=false`, `FUELLEDGER_ALLOWED_HOSTS=fuelledger.co.za,www.fuelledger.co.za`, and `FUELLEDGER_CSRF_TRUSTED_ORIGINS=https://fuelledger.co.za,https://www.fuelledger.co.za`. Set the `FUELLEDGER_DATABASE_*` values to the dedicated database credentials and host shown in cPanel. Never commit `.env` to GitHub.

The `~/Fuelledger/deploy-cpanel.sh` script is separate from any existing `~/deploy.sh`. It pulls `main`, installs requirements into `~/virtualenv/Fuelledger/3.11`, applies migrations, collects static files, and restarts Passenger in place. Run it after pushing changes with `bash ~/Fuelledger/deploy-cpanel.sh`. For the first deployment, run `cd ~/Fuelledger && source ~/virtualenv/Fuelledger/3.11/bin/activate && python manage.py createsuperuser`.

This mirrors a manual cPanel deploy: pushing to GitHub does not by itself run the server's deploy script. To automate that later, GitHub Actions can connect to cPanel over SSH and invoke this script.