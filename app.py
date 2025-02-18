from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import pandas as pd
import io
import uuid
import secrets
from functools import wraps
from google.oauth2.credentials import Credentials

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build('sheets', 'v4', credentials=credentials)
sheet = service.spreadsheets()

# Your spreadsheet ID
SPREADSHEET_ID = os.getenv('GOOGLE_SHEET_ID')

def calculate_emi(principal, interest_rate, tenure_months):
    try:
        principal = float(principal)
        interest_rate = float(interest_rate)
        tenure_months = int(tenure_months)
        
        # Monthly interest rate
        monthly_rate = (interest_rate / 100) / 12
        
        # Monthly principal (simple division for equal principal payments)
        monthly_principal = principal / tenure_months
        
        # Monthly interest on remaining principal
        monthly_interest = (principal * monthly_rate)
        
        # Total EMI
        emi = monthly_principal + monthly_interest
        
        return {
            'emi': round(emi, 2),
            'monthly_principal': round(monthly_principal, 2),
            'monthly_interest': round(monthly_interest, 2)
        }
    except (ValueError, ZeroDivisionError) as e:
        print(f"Error in EMI calculation: {str(e)}")
        return {
            'emi': 0.00,
            'monthly_principal': 0.00,
            'monthly_interest': 0.00
        }


def get_loan_progress(loan_id):
    loan = get_sheet_data(f'Loans!A{loan_id + 2}:E{loan_id + 2}')[0]
    payments = get_sheet_data('Payments!A2:D')
    total_paid = sum(float(payment[1])
                     for payment in payments if payment[0] == loan[0])
    return min(round((total_paid / float(loan[1])) * 100, 2), 100)


def generate_share_link(borrower_id):
    # Generate a unique token for the borrower
    token = str(uuid.uuid4())
    # Store the token in the borrower's data
    update_sheet_data(f'Borrowers!D{borrower_id + 2}', [[token]])
    return token


def setup_sheets():
    # Define the sheets and their headers
    sheets_data = {
        'Borrowers': ['Name', 'Address', 'Token'],
        'Loans': ['Borrower', 'Amount', 'Interest Rate', 'Start Date', 'Tenure', 'EMI', 'Status'],
        'Payments': ['Borrower', 'Total Amount', 'Date', 'Principal Amount', 'Interest Amount', 'Penalty Amount', 'Notes']
    }

    try:
        # Get existing sheets
        spreadsheet = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
        existing_sheets = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]

        # Create sheets if they don't exist
        batch_update_requests = []
        for sheet_name in sheets_data:
            if sheet_name not in existing_sheets:
                batch_update_requests.append({
                    'addSheet': {
                        'properties': {
                            'title': sheet_name
                        }
                    }
                })

        if batch_update_requests:
            sheet.batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={'requests': batch_update_requests}
            ).execute()

        # Update headers for each sheet
        for sheet_name, headers in sheets_data.items():
            # Get the sheet ID
            sheet_id = get_sheet_id(sheet_name)
            if not sheet_id:
                continue

            # Update headers
            range_name = f'{sheet_name}!A1:{chr(65 + len(headers) - 1)}1'
            sheet.values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name,
                valueInputOption='RAW',
                body={'values': [headers]}
            ).execute()

            # Format headers
            requests = [
                # Header formatting
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': 0,
                            'endRowIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.2, 'green': 0.2, 'blue': 0.2},
                                'textFormat': {
                                    'foregroundColor': {'red': 1, 'green': 1, 'blue': 1},
                                    'bold': True
                                }
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat)'
                    }
                },
                # Freeze header row
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'gridProperties': {
                                'frozenRowCount': 1
                            }
                        },
                        'fields': 'gridProperties.frozenRowCount'
                    }
                },
                # Auto-resize columns
                {
                    'autoResizeDimensions': {
                        'dimensions': {
                            'sheetId': sheet_id,
                            'dimension': 'COLUMNS',
                            'startIndex': 0,
                            'endIndex': len(headers)
                        }
                    }
                }
            ]

            # Add number format for amount columns if it's the Payments or Loans sheet
            if sheet_name in ['Payments', 'Loans']:
                amount_columns = []
                if sheet_name == 'Payments':
                    amount_columns = [1, 3, 4, 5]  # Total Amount, Principal, Interest, Penalty columns
                elif sheet_name == 'Loans':
                    amount_columns = [1, 2, 5]  # Amount, Interest Rate, EMI columns

                for col_index in amount_columns:
                    requests.append({
                        'repeatCell': {
                            'range': {
                                'sheetId': sheet_id,
                                'startRowIndex': 1,
                                'startColumnIndex': col_index,
                                'endColumnIndex': col_index + 1
                            },
                            'cell': {
                                'userEnteredFormat': {
                                    'numberFormat': {
                                        'type': 'NUMBER',
                                        'pattern': '#,##0.00'
                                    }
                                }
                            },
                            'fields': 'userEnteredFormat.numberFormat'
                        }
                    })

            # Apply all formatting
            sheet.batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={'requests': requests}
            ).execute()

        return True
    except Exception as e:
        print(f"Error setting up sheets: {str(e)}")
        return False


def get_sheet_id(sheet_name):
    spreadsheet = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    for sheet_data in spreadsheet['sheets']:
        if sheet_data['properties']['title'] == sheet_name:
            return sheet_data['properties']['sheetId']
    return None

# User class for Flask-Login


class User(UserMixin):
    def __init__(self, id):
        self.id = id


@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# Helper functions for Google Sheets


def get_sheet_data(range_name):
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueRenderOption='UNFORMATTED_VALUE'
        ).execute()
        values = result.get('values', [])
        
        # Debug print
        print(f"Raw data from {range_name}:", values)
        
        # Clean and format the data
        cleaned_values = []
        for row in values:
            # Pad row with None if it's shorter than expected
            while len(row) < 7:  # We expect 7 columns
                row.append(None)
                
            # Convert numeric strings to float only for amount fields
            try:
                # Only convert amount fields based on the sheet type
                if 'Payments!' in range_name:
                    # For Payments sheet
                    if len(row) > 1 and row[1]: # Total Amount
                        row[1] = float(str(row[1]).replace('₹', '').replace(',', '').strip() or 0)
                    if len(row) > 3 and row[3]: # Principal
                        row[3] = float(str(row[3]).replace('₹', '').replace(',', '').strip() or 0)
                    if len(row) > 4 and row[4]: # Interest
                        row[4] = float(str(row[4]).replace('₹', '').replace(',', '').strip() or 0)
                    if len(row) > 5 and row[5]: # Penalty
                        row[5] = float(str(row[5]).replace('₹', '').replace(',', '').strip() or 0)
                elif 'Loans!' in range_name:
                    # For Loans sheet
                    if len(row) > 1 and row[1]: # Amount
                        row[1] = float(str(row[1]).replace('₹', '').replace(',', '').strip() or 0)
                    if len(row) > 2 and row[2]: # Interest Rate
                        row[2] = float(str(row[2]).replace('%', '').strip() or 0)
                    if len(row) > 5 and row[5]: # EMI
                        row[5] = float(str(row[5]).replace('₹', '').replace(',', '').strip() or 0)
            except (ValueError, TypeError) as e:
                print(f"Error converting numeric values in row {row}: {str(e)}")
                
            cleaned_values.append(row)
            
        # Debug print
        print(f"Cleaned data from {range_name}:", cleaned_values)
        
        return cleaned_values
        
    except Exception as e:
        print(f"Error getting sheet data: {str(e)}")
        return []


def append_sheet_data(range_name, values):
    try:
        # Ensure values is a list of lists
        if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
            raise ValueError("Values must be a list of lists")

        # Convert all values to strings and remove any currency symbols or commas
        formatted_values = []
        for row in values:
            formatted_row = []
            for value in row:
                if isinstance(value, (int, float)):
                    formatted_row.append(str(value))
                else:
                    # Remove currency symbols and commas from string values
                    formatted_value = str(value).replace('₹', '').replace(',', '').strip()
                    formatted_row.append(formatted_value)
            formatted_values.append(formatted_row)

        body = {'values': formatted_values}
        
        # Debug print
        print("Formatted values being sent to sheets:", formatted_values)
        
        result = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        return result
    except Exception as e:
        print(f"Error in append_sheet_data: {str(e)}")
        return None


def update_sheet_data(range_name, values):
    body = {'values': values}
    result = sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueInputOption='RAW',
        body=body
    ).execute()
    return result


def delete_row(sheet_name, row_index):
    range_name = f'{sheet_name}!A{row_index}:{chr(65 + len(get_sheet_data(f"{sheet_name}!A1:A")) - 1)}{row_index}'
    result = sheet.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        body={}
    ).execute()
    return result


def append_to_sheet(range_name, values):
    """
    Append values to the specified range in Google Sheets.
    
    Args:
        range_name (str): The range to append to (e.g., 'Sheet1!A2:C')
        values (list): List of rows to append
    """
    try:
        body = {
            'values': values
        }
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        return result
    except Exception as e:
        print(f"Error in append_to_sheet: {str(e)}")
        raise Exception(f"Failed to append data to sheet: {str(e)}")


# Call setup_sheets when the application starts
setup_sheets()

# Routes


@app.route('/')
@login_required
def index():
    try:
        # Get all borrowers
        borrowers = get_sheet_data('Borrowers!A2:C')
        total_borrowers = len(borrowers) if borrowers else 0

        # Get all loans
        loans = get_sheet_data('Loans!A2:G')

        # Get all payments
        # Updated to get all columns
        payments = get_sheet_data('Payments!A2:G')

        # Calculate active loans and total amount
        active_loans = 0
        total_loan_amount = 0
        total_principal_paid = 0

        if loans:
            for loan in loans:
                # Check status in column G
                if len(loan) >= 7 and loan[6] == 'Active':
                    active_loans += 1
                    try:
                        # Check if amount exists and is not empty
                        if loan[1] and str(loan[1]).strip():
                            total_loan_amount += float(str(loan[1]).replace(
                                '₹', '').replace(',', '').strip())
                    except (ValueError, TypeError) as e:
                        print(f"Error converting loan amount: {e}")
                        continue

        # Calculate total principal paid from payments
        if payments:
            for payment in payments:
                try:
                    # Check principal amount in column D
                    if len(payment) >= 4 and payment[3] and str(payment[3]).strip():
                        total_principal_paid += float(
                            str(payment[3]).replace('₹', '').replace(',', '').strip())
                except (ValueError, TypeError) as e:
                    print(f"Error converting payment amount: {e}")
                    continue

        # Calculate percentage of principal paid (handle division by zero)
        total_principal_paid_percentage = (
            total_principal_paid / total_loan_amount * 100) if total_loan_amount > 0 else 0

        return render_template('index.html',
                               total_borrowers=total_borrowers,
                               active_loans=active_loans,
                               total_loan_amount=total_loan_amount,
                               total_principal_paid=total_principal_paid,
                               total_principal_paid_percentage=total_principal_paid_percentage)
    except Exception as e:
        print(f"Error loading dashboard: {str(e)}")  # Add debug print
        flash(f'Error loading dashboard: {str(e)}')
        return render_template('index.html',
                               total_borrowers=0,
                               active_loans=0,
                               total_loan_amount=0,
                               total_principal_paid=0,
                               total_principal_paid_percentage=0)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == os.getenv('ADMIN_USERNAME') and password == os.getenv('ADMIN_PASSWORD'):
            user = User(username)
            login_user(user)
            session['logged_in'] = True
            session['user_id'] = user.id
            return redirect(url_for('index'))
        flash('Invalid credentials')
    return render_template('login.html')


@app.route('/logout')
def logout():
    try:
        # Clear the session
        session.clear()
        flash('You have been successfully logged out.')
        return redirect(url_for('login'))
    except Exception as e:
        print(f"Error during logout: {str(e)}")
        flash('Error during logout.')
        return redirect(url_for('index'))


@app.route('/add_borrower', methods=['GET', 'POST'])
@login_required
def add_borrower():
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            address = request.form.get('address', '').strip()

            if not name or not address:
                flash('Name and address are required')
                return redirect(url_for('add_borrower'))

            # Generate a unique token for public view
            token = secrets.token_urlsafe(16)

            # Store borrower data with token
            values = [[name, address, token]]  # Wrap in an extra list for proper formatting
            
            # Debug print
            print("Adding borrower with values:", values)
            
            result = append_sheet_data('Borrowers!A:C', values)
            
            if result:
                flash('Borrower added successfully')
            else:
                flash('Error adding borrower')
            
            return redirect(url_for('add_borrower'))
            
        except Exception as e:
            print(f"Error adding borrower: {str(e)}")
            flash(f'Error adding borrower: {str(e)}')
            return redirect(url_for('add_borrower'))

    try:
        # Get all borrowers for the list
        borrowers = get_sheet_data('Borrowers!A2:C')
        # Pass the borrower_id as the index in the list
        return render_template('add_borrower.html', borrowers=borrowers, borrower_id=len(borrowers))
    except Exception as e:
        print(f"Error loading borrowers: {str(e)}")
        flash('Error loading borrowers')
        return render_template('add_borrower.html', borrowers=[], borrower_id=0)


@app.route('/add_loan', methods=['GET', 'POST'])
def add_loan():
    try:
        # Get all borrowers for the dropdown
        borrowers = get_sheet_data('Borrowers!A2:D')
        
        # Get all loans
        loans = get_sheet_data('Loans!A2:G')
        
        if request.method == 'POST':
            # Get and validate form data
            borrower_name = request.form.get('borrower_name', '').strip()
            start_date = request.form.get('start_date', '').strip()
            
            if not borrower_name or not start_date:
                flash('Borrower name and start date are required')
                return redirect(url_for('add_loan'))

            # Convert and validate numeric values
            try:
                principal = float(request.form.get('amount', '0').strip())
                interest_rate = float(request.form.get('interest_rate', '0').strip())
                tenure = int(request.form.get('tenure', '0').strip())
                
                if principal <= 0 or interest_rate <= 0 or tenure <= 0:
                    flash('Amount, interest rate, and tenure must be greater than 0')
                    return redirect(url_for('add_loan'))
                    
            except ValueError as e:
                flash('Please enter valid numeric values for amount, interest rate, and tenure')
                return redirect(url_for('add_loan'))

            # Calculate EMI
            try:
                emi_details = calculate_emi(principal, interest_rate, tenure)
            except Exception as e:
                print(f"Error calculating EMI: {str(e)}")
                flash('Error calculating EMI')
                return redirect(url_for('add_loan'))

            # Format the values as a list of lists
            values = [[
                str(borrower_name),
                str(principal),
                str(interest_rate),
                str(start_date),
                str(tenure),
                str(emi_details['emi']),
                'Active'
            ]]

            # Debug print
            print("Adding loan with values:", values)

            # Append to sheet
            result = append_sheet_data('Loans!A:G', values)
            
            if result:
                flash('Loan added successfully')
            else:
                flash('Error adding loan')
            
            return redirect(url_for('add_loan'))
            
        # Get the selected borrower's details if available
        selected_borrower = request.args.get('borrower_name')
        borrower = None
        if selected_borrower:
            for b in borrowers:
                if b[0] == selected_borrower:
                    borrower = b
                    break
        
        # Calculate loan progress for each loan
        loan_progress = []
        if loans:
            payments = get_sheet_data('Payments!A2:G')
            for loan in loans:
                if loan[6] == 'Active':  # Only for active loans
                    loan_payments = [p for p in payments if p[0] == loan[0]]
                    total_paid = sum(float(p[3]) for p in loan_payments)  # Sum of principal payments
                    progress = min(100, (total_paid / float(loan[1])) * 100)
                    loan_progress.append(round(progress, 2))
                else:
                    loan_progress.append(100)
        
        return render_template('add_loan.html', 
                             borrowers=borrowers,
                             borrower=borrower if borrower else ['', '', '', ''],
                             loans=loans,
                             loan_progress=loan_progress)
    except Exception as e:
        print(f"Error in add_loan: {str(e)}")
        flash('Error loading add loan page')
        return redirect(url_for('index'))


@app.route('/add_payment', methods=['GET', 'POST'])
def add_payment():
    if request.method == 'POST':
        try:
            # Get form data
            borrower_name = request.form.get('borrower_name')
            payment_date = request.form.get('payment_date')
            notes = request.form.get('notes', '')

            # Validate required fields
            if not borrower_name:
                return jsonify({
                    'success': False,
                    'message': 'Borrower name is required'
                })

            if not payment_date:
                return jsonify({
                    'success': False,
                    'message': 'Payment date is required'
                })

            # Convert and validate amounts
            try:
                principal_amount = float(request.form.get('principal_amount', 0))
                interest_amount = float(request.form.get('interest_amount', 0))
                penalty_amount = float(request.form.get('penalty_amount', 0))
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': 'Invalid amount values provided'
                })

            # Validate at least one amount is greater than 0
            if principal_amount <= 0 and interest_amount <= 0 and penalty_amount <= 0:
                return jsonify({
                    'success': False,
                    'message': 'At least one payment amount must be greater than 0'
                })

            # Calculate total amount
            total_amount = principal_amount + interest_amount + penalty_amount

            # Prepare payment data
            payment_data = [[
                borrower_name,
                str(total_amount),
                payment_date,
                str(principal_amount),
                str(interest_amount),
                str(penalty_amount),
                notes
            ]]

            # Debug print
            print("Adding payment with data:", payment_data)

            # Append to sheet
            result = append_sheet_data('Payments!A:G', payment_data)

            if result:
                return jsonify({
                    'success': True,
                    'message': 'Payment added successfully'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to add payment to the sheet'
                })

        except Exception as e:
            print(f"Error adding payment: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'Error adding payment: {str(e)}'
            })

    # GET request - show payment form
    try:
        borrowers = get_sheet_data('Borrowers!A2:A')
        payments = get_sheet_data('Payments!A2:G')
        
        # Sort payments by date in descending order
        if payments:
            try:
                payments.sort(key=lambda x: datetime.strptime(x[2], '%Y-%m-%d'), reverse=True)
            except Exception as e:
                print(f"Error sorting payments: {str(e)}")

        return render_template('add_payment.html', borrowers=borrowers, payments=payments)
    except Exception as e:
        print(f"Error loading payment page: {str(e)}")
        flash('Error loading payment page')
        return render_template('add_payment.html', borrowers=[], payments=[])


@app.route('/borrower/edit/<int:borrower_id>', methods=['GET', 'POST'])
@login_required
def edit_borrower(borrower_id):
    try:
        # Get all borrowers to verify the index
        all_borrowers = get_sheet_data(
            'Borrowers!A2:D')  # Include token column

        if not all_borrowers or borrower_id >= len(all_borrowers):
            flash('Borrower not found')
            return redirect(url_for('index'))

        # Get the specific borrower
        borrower = all_borrowers[borrower_id]

        # Ensure borrower array has all required fields
        while len(borrower) < 3:
            borrower.append('')

        if request.method == 'POST':
            # Get form data
            name = request.form.get('name', '').strip()
            address = request.form.get('address', '').strip()

            if not name or not address:
                flash('Name and address are required')
                return render_template('edit_borrower.html',
                                       borrower=borrower,
                                       borrower_id=borrower_id)

            # Update the borrower in the sheet
            row_number = borrower_id + 2  # Add 2 to account for header row
            update_values = [
                [name, address, borrower[2] if len(borrower) > 2 else '']]

            update_sheet_data(
                f'Borrowers!A{row_number}:C{row_number}', update_values)

            flash('Borrower updated successfully')
            return redirect(url_for('index'))

        return render_template('edit_borrower.html',
                               borrower=borrower,
                               borrower_id=borrower_id)

    except Exception as e:
        print(f"Error in edit_borrower: {str(e)}")
        flash(f'Error editing borrower: {str(e)}')
        return redirect(url_for('index'))


@app.route('/borrower/delete/<int:borrower_id>')
@login_required
def delete_borrower(borrower_id):
    try:
        # Check if borrower exists
        borrower_data = get_sheet_data(
            f'Borrowers!A{borrower_id + 2}:A{borrower_id + 2}')
        if not borrower_data or len(borrower_data) == 0:
            flash('Borrower not found')
            return redirect(url_for('add_borrower'))

        borrower_name = borrower_data[0][0]

        # Check if borrower has active loans
        loans = get_sheet_data('Loans!A2:G')
        if any(loan[0] == borrower_name and loan[6] == 'Active' for loan in loans):
            flash('Cannot delete borrower with active loans')
            return redirect(url_for('add_borrower'))

        delete_row('Borrowers', borrower_id + 2)
        flash('Borrower deleted successfully')
        return redirect(url_for('add_borrower'))
    except Exception as e:
        flash(f'Error deleting borrower: {str(e)}')
        return redirect(url_for('add_borrower'))


@app.route('/edit_loan/<loan_id>', methods=['GET', 'POST'])
def edit_loan(loan_id):
    try:
        # Get all borrowers for the dropdown
        borrowers = get_sheet_data('Borrowers!A2:D')
        
        # Get all loans
        loans = get_sheet_data('Loans!A2:G')
        loan = None
        loan_index = None
        
        # Find the loan and its index
        for index, l in enumerate(loans, start=2):  # start=2 because data starts from A2
            if str(l[0]) == str(loan_id):  # Convert both to string for comparison
                loan = l
                loan_index = index
                break
                
        if not loan:
            flash('Loan not found')
            return redirect(url_for('index'))

        if request.method == 'POST':
            try:
                # Get form data
                borrower_name = request.form.get('borrower_name')
                amount = request.form.get('amount')
                interest_rate = request.form.get('interest_rate')
                start_date = request.form.get('start_date')
                tenure = request.form.get('tenure')
                
                # Validate data
                if not all([borrower_name, amount, interest_rate, start_date, tenure]):
                    flash('All fields are required')
                    return render_template('edit_loan.html', loan=loan, loan_id=loan_id, borrowers=borrowers)
                
                # Convert to appropriate types
                amount = float(amount)
                interest_rate = float(interest_rate)
                tenure = int(tenure)
                
                # Calculate EMI
                emi_details = calculate_emi(amount, interest_rate, tenure)
                emi = emi_details['emi']
                
                # Prepare values for update
                update_range = f'Loans!A{loan_index}:G{loan_index}'
                values = [[
                    borrower_name,
                    str(amount),
                    str(interest_rate),
                    start_date,
                    str(tenure),
                    str(round(emi, 2)),
                    'Active'
                ]]
                
                # Update the sheet
                result = update_sheet_data(update_range, values)
                
                if result:
                    flash('Loan updated successfully')
                    return redirect(url_for('add_loan'))
                else:
                    flash('Error updating loan. Please try again.')
                    return render_template('edit_loan.html', loan=loan, loan_id=loan_id, borrowers=borrowers)
                
            except ValueError as ve:
                print(f"Value Error in edit_loan POST: {str(ve)}")
                flash('Please enter valid numbers for amount, interest rate, and tenure')
                return render_template('edit_loan.html', loan=loan, loan_id=loan_id, borrowers=borrowers)
                
            except Exception as e:
                print(f"Error in edit_loan POST: {str(e)}")
                flash('Error updating loan. Please try again.')
                return render_template('edit_loan.html', loan=loan, loan_id=loan_id, borrowers=borrowers)
        
        return render_template('edit_loan.html', 
                             loan=loan,
                             loan_id=loan_id,
                             borrowers=borrowers)
                             
    except Exception as e:
        print(f"Error in edit_loan: {str(e)}")
        flash('Error loading loan details')
        return redirect(url_for('index'))


@app.route('/loan/delete/<int:loan_id>')
@login_required
def delete_loan(loan_id):
    loan = get_sheet_data(f'Loans!A{loan_id + 2}:G{loan_id + 2}')[0]
    if loan[6] == 'Active':
        flash('Cannot delete active loan')
        return redirect(url_for('index'))

    delete_row('Loans', loan_id + 2)
    flash('Loan deleted successfully')
    return redirect(url_for('index'))


@app.route('/calculate_emi')
def calculate_emi_route():
    principal = float(request.args.get('principal', 0))
    interest_rate = float(request.args.get('interest_rate', 0))
    tenure = int(request.args.get('tenure', 0))
    return jsonify(calculate_emi(principal, interest_rate, tenure))


@app.route('/public_view/<borrower_name>')
def public_view(borrower_name):
    try:
        # Get borrower details
        borrowers = get_sheet_data('Borrowers!A2:C')
        borrower = None
        for b in borrowers:
            if b[0] == borrower_name:
                borrower = b
                break

        if not borrower:
            flash('Borrower not found')
            return redirect(url_for('index'))

        # Get loans for this borrower
        loans = get_sheet_data('Loans!A2:G')
        borrower_loans = [loan for loan in loans if loan[0] == borrower_name]

        # Get payments for this borrower
        payments = get_sheet_data('Payments!A2:G')  # Changed to include all columns
        borrower_payments = [payment for payment in payments if payment[0] == borrower_name]

        # Calculate loan progress for each loan
        loan_progress = []
        for loan in borrower_loans:
            try:
                loan_payments = [p for p in borrower_payments]
                total_paid = sum(float(p[1]) for p in loan_payments if p[1])  # Use total amount paid
                loan_amount = float(loan[1]) if loan[1] else 0
                progress = (total_paid / loan_amount * 100) if loan_amount > 0 else 0
                loan_progress.append(min(100, round(progress, 2)))
            except (ValueError, TypeError, ZeroDivisionError) as e:
                print(f"Error calculating loan progress: {str(e)}")
                loan_progress.append(0)

        # Sort payments by date in descending order
        if borrower_payments:
            try:
                borrower_payments.sort(key=lambda x: datetime.strptime(str(x[2]), '%Y-%m-%d'), reverse=True)
            except Exception as e:
                print(f"Error sorting payments: {str(e)}")

        return render_template('public_view.html',
                            borrower=borrower,
                            loans=borrower_loans,
                            payments=borrower_payments,
                            loan_progress=loan_progress)

    except Exception as e:
        print(f"Error in public_view: {str(e)}")
        flash('Error loading public view')
        return redirect(url_for('index'))


@app.route('/export/<token>')
def export_data(token):
    # Find borrower by token
    borrowers = get_sheet_data('Borrowers!A2:D')
    borrower_index = None
    borrower = None
    for i, b in enumerate(borrowers):
        if len(b) > 3 and b[3] == token:
            borrower_index = i
            borrower = b
            break

    if borrower_index is None:
        return "Invalid or expired link", 404

    # Get borrower's data
    loans = get_sheet_data('Loans!A2:G')
    payments = get_sheet_data('Payments!A2:E')

    borrower_loans = [loan for loan in loans if loan[0] == borrower[0]]
    borrower_payments = [
        payment for payment in payments if payment[0] == borrower[0]]

    # Create DataFrames
    loans_df = pd.DataFrame(borrower_loans, columns=[
                            'Borrower', 'Amount', 'Interest Rate', 'Start Date', 'Tenure', 'EMI', 'Status'])
    payments_df = pd.DataFrame(borrower_payments, columns=[
                               'Borrower', 'Amount', 'Date', 'Type', 'Notes'])

    # Create Excel file in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        loans_df.to_excel(writer, sheet_name='Loans', index=False)
        payments_df.to_excel(writer, sheet_name='Payments', index=False)

    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'loan_data_{datetime.now().strftime("%Y%m%d")}.xlsx'
    )


@app.route('/borrower/share/<int:borrower_id>')
@login_required
def share_borrower(borrower_id):
    try:
        # Get borrower data
        borrowers = get_sheet_data('Borrowers!A2:C')
        if not borrowers or borrower_id >= len(borrowers):
            return jsonify({'success': False, 'message': 'Borrower not found'})

        borrower = borrowers[borrower_id]
        borrower_name = borrower[0]

        # Generate token and update borrower's data
        token = generate_share_link(borrower_id)
        
        # Generate share URL using borrower's name
        share_url = url_for('public_view', borrower_name=borrower_name, _external=True)
        return jsonify({'success': True, 'share_url': share_url})
    except Exception as e:
        print(f"Error generating share link: {str(e)}")
        return jsonify({'success': False, 'message': f'Error generating share link: {str(e)}'})


@app.route('/borrower/<int:borrower_id>')
def borrower_details(borrower_id):
    try:
        # Get borrower data
        borrowers = get_sheet_data('Borrowers!A2:C')
        if not borrowers or borrower_id >= len(borrowers):
            flash('Borrower not found')
            return redirect(url_for('index'))

        borrower = borrowers[borrower_id]

        # Get loans for this borrower
        loans = get_sheet_data('Loans!A2:G')
        if loans:
            # Filter loans for this borrower
            borrower_loans = [loan for loan in loans if loan[0] == borrower[0]]
        else:
            borrower_loans = []

        # Calculate totals
        total_loan_amount = sum(
            float(loan[1]) for loan in borrower_loans if loan[1]) if borrower_loans else 0

        # Get payments
        payments = get_sheet_data('Payments!A2:G')
        if payments:
            # Filter payments for this borrower
            borrower_payments = [
                payment for payment in payments if payment[0] == borrower[0]]
        else:
            borrower_payments = []

        # Initialize payment totals
        total_principal = 0
        total_interest = 0
        total_penalty = 0
        total_amount_paid = 0

        if borrower_payments:
            for payment in borrower_payments:
                try:
                    # Principal (column D)
                    if len(payment) > 3 and payment[3] and str(payment[3]).strip():
                        total_principal += float(
                            str(payment[3]).replace(',', ''))

                    # Interest (column E)
                    if len(payment) > 4 and payment[4] and str(payment[4]).strip():
                        total_interest += float(
                            str(payment[4]).replace(',', ''))

                    # Penalty (column F)
                    if len(payment) > 5 and payment[5] and str(payment[5]).strip():
                        total_penalty += float(str(payment[5]
                                                   ).replace(',', ''))

                    # Total amount (column B)
                    if len(payment) > 1 and payment[1] and str(payment[1]).strip():
                        total_amount_paid += float(
                            str(payment[1]).replace(',', ''))
                except (ValueError, TypeError) as e:
                    print(f"Error processing payment: {e}")
                    continue

        # Calculate active loans count
        active_loans_count = sum(
            1 for loan in borrower_loans if loan[6] == 'Active') if borrower_loans else 0

        # Calculate loan progress
        loan_progress = []
        for loan in borrower_loans:
            try:
                if loan[1] and total_amount_paid:
                    progress = (float(total_amount_paid) /
                                float(loan[1])) * 100
                    # Ensure progress is between 0 and 100
                    progress = min(100, max(0, progress))
                else:
                    progress = 0
                loan_progress.append(round(progress, 2))
            except (ValueError, TypeError):
                loan_progress.append(0)

        return render_template('borrower_details.html',
                               borrower=borrower,
                               borrower_id=borrower_id,
                               loans=borrower_loans,
                               payments=borrower_payments,
                               active_loans_count=active_loans_count,
                               total_loan_amount=total_loan_amount,
                               total_amount_paid=total_amount_paid,
                               total_principal=total_principal,
                               total_interest=total_interest,
                               total_penalty=total_penalty,
                               loan_progress=loan_progress)

    except Exception as e:
        print(f"Error loading borrower details: {str(e)}")
        flash(f'Error loading borrower details: {str(e)}')
        return redirect(url_for('index'))


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please log in to access this page.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def excel_date_to_datetime(excel_date):
    if isinstance(excel_date, str):
        try:
            # Try to parse as YYYY-MM-DD first
            return datetime.strptime(excel_date, '%Y-%m-%d')
        except ValueError:
            try:
                # Try to convert Excel numeric date
                excel_date = float(excel_date)
                delta = timedelta(days=excel_date - 25569)  # Excel to Unix epoch
                return datetime(1970, 1, 1) + delta
            except (ValueError, TypeError):
                return None
    elif isinstance(excel_date, (int, float)):
        delta = timedelta(days=excel_date - 25569)  # Excel to Unix epoch
        return datetime(1970, 1, 1) + delta
    return None

@app.template_filter('to_datetime')
def to_datetime(date_str):
    if not date_str:
        return None
    return excel_date_to_datetime(date_str)

@app.template_filter('strftime')
def strftime(date, format='%Y-%m-%d'):
    if not date:
        return ''
    if isinstance(date, datetime):
        return date.strftime(format)
    converted_date = excel_date_to_datetime(date)
    if converted_date:
        return converted_date.strftime(format)
    return str(date)

# Add timedelta to Jinja globals
app.jinja_env.globals['timedelta'] = timedelta

# Add filters to Jinja environment
app.jinja_env.filters['to_datetime'] = to_datetime
app.jinja_env.filters['strftime'] = strftime

if __name__ == '__main__':
    app.run(debug=True)
