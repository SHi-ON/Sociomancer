import json
import logging
import os
import time
from datetime import datetime
from time import sleep

from bs4 import BeautifulSoup
from playwright.sync_api import Playwright, sync_playwright

LINKEDIN_SEARCH_PAGE_URL = os.getenv('LINKEDIN_SEARCH_PAGE_URL')
LINKEDIN_EMAIL = os.getenv('LINKEDIN_EMAIL')
LINKEDIN_PASSWORD = os.getenv('LINKEDIN_PASSWORD')

LOCATION_CONSTRAINT_1 = os.getenv('LOCATION_CONSTRAINT_1', 'NO_CONSTRAINT_1')
LOCATION_CONSTRAINT_2 = os.getenv('LOCATION_CONSTRAINT_2', 'NO_CONSTRAINT_2')
LOCATION_CONSTRAINT_3 = os.getenv('LOCATION_CONSTRAINT_3', 'NO_CONSTRAINT_3')


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    print("Logging in to LinkedIn...")
    page.goto(LINKEDIN_SEARCH_PAGE_URL)
    page.get_by_role("textbox", name="Email or phone").fill(LINKEDIN_EMAIL)
    page.get_by_role("textbox", name="Password").fill(LINKEDIN_PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()

    print("Waiting for the page to load completely...")
    time.sleep(10)

    html_content = page.content()
    soup = BeautifulSoup(html_content, 'html.parser')

    print("Extracting profile URLs using the CSS selector...")
    profile_links = [a['href'] for a in soup.select(
        'a.eLkeKfLANpCeIaaehlTorHkfbdUHqQFXesk[href*="/in/"]')]

    if not profile_links:
        print(
            "No links found with the specific class. Trying a more general selector...")
        elements = page.query_selector_all('a[href*="linkedin.com/in/"]')
        profile_links = [element.get_attribute('href') for element in elements]

        if not profile_links:
            print("Trying another selector pattern...")
            elements = page.query_selector_all(
                'a[href*="/in/"][data-control-name="search_srp_result"]')
            profile_links = [element.get_attribute('href') for element in
                             elements]

    print(f"Found {len(profile_links)} profile links")

    processed_profiles = {}
    save_file = "processed_profiles.json"

    if os.path.exists(save_file):
        try:
            with open(save_file, 'r') as f:
                processed_profiles = json.load(f)
            print(
                f"Loaded {len(processed_profiles)} previously processed profiles")
        except Exception as e:
            print(f"Error loading processed profiles: {str(e)}")

    for i, profile_url in enumerate(profile_links):
        if 'azdam' in profile_url or profile_url in processed_profiles:
            print(f"Skipping already processed profile: {profile_url}")
            continue

        print(f"Opening ({i + 1}/{len(profile_links)}): {profile_url}")
        profile_page = context.new_page()
        profile_page.goto(profile_url)
        time.sleep(1)

        processed_profiles[profile_url] = {
            "timestamp": datetime.now().isoformat(),
            "status": "opened"
        }

        element = profile_page.query_selector(
            'span.text-body-small.inline.t-black--light.break-words')
        if element:
            text_content = element.inner_text().strip().lower()
            print("Captured text:", text_content)

            if (LOCATION_CONSTRAINT_1 in text_content or
                    LOCATION_CONSTRAINT_2 in text_content or
                    LOCATION_CONSTRAINT_3 in text_content):
                print("Location matches expected text.")
            else:
                logging.warning(f'Location text is different: {text_content}')
                continue
        else:
            logging.warning("Element not found.")
            continue

        time.sleep(1)
        try:
            profile_page.get_by_role("button",
                                     name="More actions").click()
            sleep(1)
            profile_page.get_by_role("button",
                                     name="Remove your connection to").click()
            sleep(1)
            print(f"Successfully removed connection for: {profile_url}")
            processed_profiles[profile_url]["status"] = "removed"
        except Exception as e:
            print(f"Could not remove connection for {profile_url}: {str(e)}")
            processed_profiles[profile_url]["status"] = "error"
            processed_profiles[profile_url]["error"] = str(e)

        try:
            with open(save_file, 'w') as f:
                json.dump(processed_profiles, f, indent=2)
        except Exception as e:
            print(f"Error saving progress: {str(e)}")

        profile_page.close()

    input("All profiles processed. Press Enter to close the browser...")
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
