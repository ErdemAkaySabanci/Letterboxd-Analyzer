# Letterboxd ML Analyzer & Dashboard

An advanced, end-to-end data science portfolio project that scrapes your personal Letterboxd profile and performs deep statistical and machine learning analysis on your movie-watching habits.

## Features

*   **Automated Web Scraping:** Uses Playwright and BeautifulSoup to parse your Letterboxd profile, extracting ratings, watch dates, and deep metadata (Actors, Directors, Country, Language, Runtime) directly from LD+JSON scripts.
*   **Bayesian Statistics:** Calculates Bayesian Averages to fairly rank Directors and Actors (accounting for sample size and global rating means).
*   **Machine Learning (Random Forest):** Predicts how you would rate any unwatched movie based on your historical preferences (Genre, Actor one-hot encoding, Director habits, Runtime, and Release Year).
*   **Temporal & Habit Analysis:** Tracks your "taste evolution" over the years, identifies binge-watching months, and analyzes your "backlog" habits (do you watch new releases or classics?).
*   **Premium Dashboard UI:** A custom FastAPI backend serving a stunning, glassmorphism-styled dashboard using Vanilla JS and Chart.js.

## Tech Stack
*   **Backend:** Python (FastAPI)
*   **Data Science:** Pandas, NumPy, Scikit-Learn, SciPy
*   **Scraping:** Playwright, BeautifulSoup4
*   **Frontend:** HTML5, CSS3 (Glassmorphism design), Vanilla JavaScript, Chart.js

## How to Run

1. Install dependencies:
   ```bash
   pip install pandas numpy scikit-learn scipy fastapi uvicorn playwright beautifulsoup4
   playwright install
   ```

2. Start the API & Dashboard Server:
   ```bash
   python server.py
   ```

3. Open `http://localhost:8000` in your browser.
4. Enter your Letterboxd username to begin the real-time scraping and analysis process!
