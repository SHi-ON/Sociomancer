"""
LinkedIn Location Discovery Module
=================================

This module automates the process of extracting location information from LinkedIn profiles
of your connections and updating a CSV file with this information.

Overview:
---------
The script reads a CSV file containing LinkedIn connection data, visits each profile URL,
extracts the location information, and updates the CSV file with a new 'Location' column.
It processes connections from bottom to top of the CSV file and tracks progress to avoid
reprocessing profiles if the script is interrupted and restarted.

Prerequisites:
-------------
1. LinkedIn account credentials
2. Playwright Python package installed
3. A CSV file with LinkedIn connections (exported from LinkedIn)
4. The CSV file must have a 'URL' column containing LinkedIn profile URLs

Environment Setup:
----------------
Set the following environment variables before running the script:
- LINKEDIN_EMAIL: Your LinkedIn account email
- LINKEDIN_PASSWORD: Your LinkedIn account password

CSV File Format:
--------------
The CSV file should have at least the following column:
- URL: The LinkedIn profile URL of the connection

The script will add a new column:
- Location: The extracted location from the LinkedIn profile

How to Run:
----------
1. Ensure the prerequisites are met and environment variables are set
2. Place your LinkedIn connections CSV file in the same directory as this script
   (named "Connections.csv")
3. Run the script: python discover_location.py

Process:
-------
1. The script logs into LinkedIn using the provided credentials
2. It processes each connection from the bottom to the top of the CSV file
3. For each connection, it:
   a. Visits the LinkedIn profile URL
   b. Extracts the location information
   c. Updates the CSV file with the location
   d. Saves progress to a JSON file
4. Progress is tracked in "location_progress.json" to allow resuming if interrupted

Output:
------
1. Updated CSV file with a new 'Location' column
2. Log file "discover_location.log" with detailed execution information
3. Progress file "location_progress.json" tracking which profiles have been processed

Notes:
-----
- The script uses a browser automation approach, so a browser window will open during execution
- Processing may take time depending on the number of connections
- LinkedIn may have rate limits or detect automation, so use responsibly
- The script includes error handling and will mark profiles with errors as "Error" or "Not found"
"""

import csv
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Playwright, sync_playwright

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("discover_location.log"),
        logging.StreamHandler()
    ]
)

# LinkedIn credentials from environment variables
LINKEDIN_EMAIL = os.getenv('LINKEDIN_EMAIL')
LINKEDIN_PASSWORD = os.getenv('LINKEDIN_PASSWORD')

# Path to the CSV file
CSV_FILE = Path(__file__).parent / "Connections.csv"
# Path to save progress
PROGRESS_FILE = Path(__file__).parent / "location_progress.json"


def count_csv_lines():
    """Count the number of lines in the CSV file."""
    try:
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            # Count lines but subtract 1 for the header
            line_count = sum(1 for _ in f) - 1
        logging.info(f"CSV file has {line_count} data lines")
        return line_count
    except Exception as e:
        logging.error(f"Error counting CSV lines: {str(e)}")
        return 0


def get_csv_fieldnames():
    """Get the field names from the CSV file."""
    try:
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            if 'Location' not in fieldnames:
                fieldnames.append('Location')
            return fieldnames
    except Exception as e:
        logging.error(f"Error getting CSV fieldnames: {str(e)}")
        return []


def read_csv_line(line_number):
    """Read a specific line from the CSV file."""
    try:
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Skip to the desired line
            for i, row in enumerate(reader):
                if i == line_number:
                    return row
        return None
    except Exception as e:
        logging.error(f"Error reading CSV line {line_number}: {str(e)}")
        return None


def update_csv_line(line_number, updated_row, fieldnames):
    """Update a specific line in the CSV file."""
    try:
        # Read all lines from the file
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            lines = list(csv.reader(f))

        # Convert the updated row to a list in the same order as fieldnames
        row_list = [updated_row.get(field, '') for field in fieldnames]

        # Update the specific line (add 1 to account for header)
        lines[line_number + 1] = row_list

        # Write all lines back to the file
        with open(CSV_FILE, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(lines)

        logging.info(f"Successfully updated line {line_number} in CSV")
        return True
    except Exception as e:
        logging.error(f"Error updating CSV line {line_number}: {str(e)}")
        return False


def load_progress():
    """Load progress from the progress file."""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading progress: {str(e)}")
    return {}


def save_progress(progress):
    """Save progress to the progress file."""
    try:
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(progress, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving progress: {str(e)}")


def initialize_browser(playwright):
    """Initialize the browser and return the page object."""
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    return browser, context, page


def login_to_linkedin(page):
    """Login to LinkedIn using the provided credentials."""
    logging.info("Logging in to LinkedIn...")
    page.goto("https://www.linkedin.com/login")
    page.get_by_role("textbox", name="Email or phone").fill(LINKEDIN_EMAIL)
    page.get_by_role("textbox", name="Password").fill(LINKEDIN_PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()

    # Wait for login to complete
    logging.info("Waiting for login to complete...")
    time.sleep(10)


def extract_location(page, url):
    """Extract location from a LinkedIn profile page."""
    try:
        # Visit the profile page
        page.goto(url)
        time.sleep(3)  # Wait for page to load

        # Extract location using the selector
        element = page.query_selector(
            'span.text-body-small.inline.t-black--light.break-words')

        if element:
            location = element.inner_text().strip()
            logging.info(f"Found location: {location}")
            return location
        else:
            logging.warning(f"Location element not found for {url}")
            return "Not found"
    except Exception as e:
        logging.error(f"Error extracting location for {url}: {str(e)}")
        return "Error"


def process_connection(page, connection, line_number, progress, fieldnames):
    """Process a single connection and update its location."""
    url = connection.get('URL', '')
    if not url:
        logging.warning(f"No URL found for connection at line {line_number}")
        return False

    # Skip if already processed
    if url in progress:
        logging.info(f"Skipping already processed profile: {url}")
        # Update the connection with the location from progress
        if 'location' in progress[url]:
            connection['Location'] = progress[url]['location']
            # Update the CSV file
            update_csv_line(line_number, connection, fieldnames)
        return True

    logging.info(f"Processing line {line_number}: {url}")

    # Extract location
    location = extract_location(page, url)

    # Update the connection with the location
    connection['Location'] = location

    # Save progress
    progress[url] = {
        "timestamp": datetime.now().isoformat(),
        "location": location
    }
    if location == "Error":
        progress[url]["error"] = "Error extracting location"
    save_progress(progress)

    # Update the CSV file immediately
    update_csv_line(line_number, connection, fieldnames)

    return True


def run(playwright: Playwright) -> None:
    """Main function to run the location discovery process."""
    # Load progress
    progress = load_progress()

    # Get CSV field names
    fieldnames = get_csv_fieldnames()
    if not fieldnames:
        logging.error("Could not get CSV field names")
        return

    # Count lines in CSV
    total_lines = count_csv_lines()
    if total_lines <= 0:
        logging.error("No data found in CSV file")
        return

    # Initialize browser
    browser, context, page = initialize_browser(playwright)

    # Login to LinkedIn
    login_to_linkedin(page)

    # Process connections from bottom to top
    try:
        for line_number in range(total_lines - 1, -1, -1):
            # Read the specific line
            connection = read_csv_line(line_number)
            if not connection:
                logging.warning(f"Could not read line {line_number}")
                continue

            # Process the connection
            process_connection(page, connection, line_number, progress,
                               fieldnames)

            # Log progress
            logging.info(
                f"Processed line {line_number} ({total_lines - line_number}/{total_lines})")
    except Exception as e:
        logging.error(f"Error in main processing loop: {str(e)}")
    finally:
        # Close browser
        context.close()
        browser.close()
        logging.info("Browser closed")


def main():
    """Entry point for the script."""
    logging.info("Starting location discovery process")

    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        logging.error(
            "LinkedIn credentials not found in environment variables")
        print(
            "Please set LINKEDIN_EMAIL and LINKEDIN_PASSWORD environment variables")
        return

    with sync_playwright() as playwright:
        run(playwright)


if __name__ == "__main__":
    main()
