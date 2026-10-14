# FuelLedger

Standalone Django application for site-based fuel station cash-up and shift records. It does not import or modify Foodline Control.

The project targets Python 3.14 and Django 6.0.1 to match Foodline's active virtual environment. Foodline's requirements file currently pins Django 5.1.6, so its declared pin differs from its installed runtime.

FuelLedger uses PostgreSQL only. It defaults to a separate database named `fuelledger`; configure its connection with the `FUELLEDGER_DATABASE_*` settings in `.env` or the process environment. Copy `.env.example` to `.env` and provide the PostgreSQL connection values for local use and deployment. Do not set its database name to Foodline's `foodlinecontrol` database.

Create a virtual environment, install `requirements.txt`, set `FUELLEDGER_SECRET_KEY`, then run `python manage.py migrate`, `python manage.py createsuperuser`, and `python manage.py runserver`.

Use the Django admin to create sites and users. A user's FuelLedger permissions section assigns their HQ/site role and whether they can configure cash-up fields. Give site users a site assignment; HQ users have no single site assignment. New sites receive a starter cash-up form the first time their ledger is opened.

## cPanel deployment

FuelLedger uses PostgreSQL and the cPanel Python 3.14 virtual environment. Keep it separate from FoodlineControl: create a dedicated PostgreSQL database and database user in cPanel/phpPgAdmin, and use an application root of `fuelledger` with the matching cPanel Python app and domain or subdomain.

Clone `Leqava09/Fuelledger` into `~/fuelledger` on the server with `main` as its branch. Create `~/fuelledger/.env` from `.env.example` and set a private `FUELLEDGER_SECRET_KEY`, `FUELLEDGER_DEBUG=false`, `FUELLEDGER_ALLOWED_HOSTS=fuelledger.co.za,www.fuelledger.co.za`, and `FUELLEDGER_CSRF_TRUSTED_ORIGINS=https://fuelledger.co.za,https://www.fuelledger.co.za`. Set the `FUELLEDGER_DATABASE_*` values to the dedicated database credentials and host shown in cPanel. Never commit `.env` to GitHub.

The `deploy-cpanel.sh` script is separate from any existing `~/deploy.sh`. It pulls `main`, installs requirements into `~/virtualenv/fuelledger/3.14`, applies migrations, collects static files, and restarts Passenger. Run it after pushing changes with `bash ~/fuelledger/deploy-cpanel.sh`. For the first deployment, run `python manage.py createsuperuser` from the same project directory after activating its virtual environment.

This mirrors a manual cPanel deploy: pushing to GitHub does not by itself run the server's deploy script. To automate that later, GitHub Actions can connect to cPanel over SSH and invoke this script.