# Loan Management Application

A Flask-based loan management system that integrates with Google Sheets for data storage.

## Deployment to Render

1. Fork this repository to your GitHub account
2. Sign up for a [Render](https://render.com) account
3. Create a new Web Service in Render and connect it to your GitHub repository
4. Configure the following environment variables in Render:
   - `FLASK_SECRET_KEY`: A secure random string for Flask sessions
   - `GOOGLE_SHEET_ID`: Your Google Sheet ID
5. Upload your `credentials.json` file in Render:
   - Go to your Web Service
   - Navigate to Environment
   - Under "Secret Files"
   - Add `credentials.json` with your Google Service Account credentials

## Local Development

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file with:
   ```
   FLASK_SECRET_KEY=your_secret_key
   GOOGLE_SHEET_ID=your_sheet_id
   ```
4. Place your `credentials.json` in the root directory
5. Run the application:
   ```bash
   python app.py
   ```

## Important Notes

- Never commit `credentials.json` to version control
- Keep your environment variables secure
- Make sure your Google Service Account has access to the Google Sheet 