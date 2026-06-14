## One script to download all comments from videos found in the youtube search

# Last N days

python youtube_search_comments.py "nike football" --days 3

# Explicit date range

python youtube_search_comments.py "nike football" --after 2024-01-01 --before 2024-06-01

# Skip videos with comments turned off

python youtube_search_comments.py "nike football" --days 30 --skip-disabled

# Custom output file

python youtube_search_comments.py "nike football" --days 2 -o nike-football-2days-comments.json

# Last N days (rolling window from today)

python youtube_search.py "Nike" --days 30

# Explicit date range

python youtube_search.py "Nike" --after 2024-01-01 --before 2024-06-01

# Only --after, no upper bound

python youtube_search.py "Nike" --after 2025-01-01

# Custom output file

python youtube_search.py "Nike" --days 7 -o ml_week.json

## Download Commands

# Using a video ID

python youtube_comments.py IyZ1WIua_1s

# Using a full URL

python youtube_comments.py "https://www.youtube.com/watch?v=IyZ1WIua_1s"

# Custom output file name

python youtube_comments.py IyZ1WIua_1s -o my_comments.json
