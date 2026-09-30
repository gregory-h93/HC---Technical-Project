import requests, re, bs4, csv, os, threading, random, time
from dateutil.parser import parse
from concurrent.futures import ThreadPoolExecutor

csv_lock = threading.Lock()
job_filename = "job_matches.csv"
job_fields = ["url", "title", "description", "location", "job_id", "job_category", "job_subcategory", "post_date", "remote_status", "work_type", "division"]

if not os.path.exists(job_filename):
    with open(job_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=job_fields)
        writer.writeheader()

INPUT_FILE = "urls.txt"

# Regex to capture the subdomain ID
pattern = re.compile(r"https?://([\s\S]+?)/", re.IGNORECASE) #https?://([^.]+)\.avature\.net

unique_ids = set()

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue

        match = pattern.search(line)
        if match:
            unique_ids.add(match.group(1))

id_list = sorted(unique_ids)

def get_avature_value(soup, label_options, url=None):
    """
    Finds the value div for the first matching label found in label_options.
    label_options: can be a single string "Location" or a list ["Category", "Business Unit"]
    """
    # Convert single string to list for consistent looping
    if isinstance(label_options, str):
        label_options = [label_options]

    for label_text in label_options:
        label = soup.find("div", class_="article__content__view__field__label",
                          string=lambda t: t and label_text.lower() in t.lower())

        if label:
            value_div = label.find_next_sibling("div", class_="article__content__view__field__value")
            if value_div:
                raw_value = value_div.get_text(strip=True)

                if any(k in ["Date", "Posted", "Published"] for k in label_options):
                    if parse:  # Only run if dateutil was imported successfully
                        try:
                            # fuzzy=True ignores extra words like "Posted on"
                            dt = parse(raw_value, fuzzy=True)
                            return dt.strftime("%Y-%m-%d")
                        except (ValueError, OverflowError):
                            return raw_value

                return raw_value

        if url and any(x in ["Ref #", "Reference", "Job ID", "Job Number"] for x in label_options):
            patterns = [
                r'jobId=(\d+)',  # jobId=12345
                r'pId=(\d+)',  # pId=12345
                r'/jobs/(\d+)',  # /jobs/12345
                r'/JobDetail/(\d+)',  # /JobDetail/12345
                r'/(\d+)(?:\?|$)'  # /12345 (at end of URL or before query params)
            ]

            for pattern in patterns:
                match = re.search(pattern, url, re.IGNORECASE)
                if match:
                    return match.group(1)

    return "N/A"


def process_single_job(link, full_path, headers):
    """Extraction logic for one job page."""
    time.sleep(random.uniform(0.5, 2.0))
    job_url = link["href"]
    if job_url.startswith("/"):
        job_url = "/".join(full_path.split("/")[:3]) + job_url

    try:
        job_response = requests.get(job_url, headers=headers, timeout=15)
        if job_response.status_code != 200:
            return None

        job_soup = bs4.BeautifulSoup(job_response.text, "html.parser")

        """
        Running through a list of "description" in the top 20 most common languages since translating to english is not always exact, 
        it is better to capture the data as it appears on the page, in its native language
        """
        description_keywords = [
            "description", "descripción", "descrição", "beschreibung", # English, French, Tagalog | Spanish | Portuguese | German
            "descrizione", "beschrijving", "описание", "विवरण",  # Italian | Dutch | Russian (Opisaniye) | Hindi (Vivaran)
            "描述", "詳細", "설명", "وصف",  # Chinese (Mandarin - Miáoshù) | Japanese (Shōsai - common on job boards) | Korean (Seol-myeong) | Arabic (Wasf)
            "opis", "açıklama", "penerangan", "bản mô tả",  # Polish, Croatian | Turkish | Malay/Indonesian | Vietnamese
            "বিবরণ", "توضیحات", "คำอธิบาย", "descriere",  # Bengali | Persian | Thai | Romanian
            "Jetzt", "General information" # Extra keywords
        ]

        tags = job_soup.select("div.section__content article, div.section__content details, div.section__content--hr-top, div.main__wrapper, div.section__content")

        description = "N/A"
        if tags:
            last_tag_text = tags[-1].get_text(separator=" ", strip=True)

            folded_text = last_tag_text.casefold()

            # Check if any full-word keyword exists in the text
            if any(key.casefold() in folded_text for key in description_keywords):
                description = last_tag_text

        if description == "N/A":
            description = tags[-1].get_text(separator=" ", strip=True) if (tags := job_soup.select("div.section__content article, div.section__content details, div.section__content--hr-top, div.main__wrapper, div.section__content")) else "N/A"

        # Build existing 'details' dictionary
        details = {
            "url": job_url,
            "title": link.get_text(strip=True),
            "description": description,
            "location": get_avature_value(job_soup, "Location"),
            "job_id": get_avature_value(job_soup, ["Ref #", "Reference", "Job ID", "Job Number"], job_url),##if not listed on the page, look in the URL
            "job_category": get_avature_value(job_soup, ["Business Unit", "Career Area", "Category", "Function", "Business Area"]),##Listed as category, business unit, etc... (Business Unit, Career Area, Category, Function, Business Area)
            "job_subcategory": get_avature_value(job_soup, "Subcategory"),
            "post_date": get_avature_value(job_soup, ["Date", "Posted", "Published"]),##(Date Published, Posted Date, Post Date)
            "remote_status": get_avature_value(job_soup, ["Remote", "Location Type"]),##Often stated in location, otherwise look for #LI-Remote/LI-Hybrid in description
            "work_type": get_avature_value(job_soup, ["Work Type", "Working Time", "Employment Class"]),##(Work Type, Working Time, Employment Class)
            "division": get_avature_value(job_soup, ["Brand", "Division"])
        }
        return details
    except Exception:
        return None

pages = sorted({'/careers/SearchJobs','/jobs'})

for _id in id_list:
    for page in pages:
        full_path = 'https://'+_id
        visited_pages = set()

        # starting page
        nextPage = full_path + page  # e.g. https://example.avature.net/jobs

        headers = {
            'Authority': _id,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-US,en;q=0.9',
            'Content-Type': 'text/html; charset=UTF-8',
            'Referer': full_path + page,
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            #'User-Agent': 'AdsBot-Google (+http://www.google.com/adsbot.html)',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        }

        while nextPage and nextPage not in visited_pages:
            visited_pages.add(nextPage)

            try:
                response = requests.get(nextPage, headers=headers, timeout=10) #timeout is added so it doesn't hang on a bad url
            except requests.exceptions.RequestException as e:
                print(f"Connection failed for {nextPage}: {e}")
                # Log the connection error to error file
                with open("connection_errors.txt", "a") as err_file:
                    err_file.write(f"ConnectionError: {nextPage}\n")
                # Break the while loop to move to the next ID/Page in the list
                break

            soup = bs4.BeautifulSoup(response.text, "html.parser")

            if response.status_code != 200:
                print(f"Failed to retrieve page: {nextPage}")
                # Log the failure to a separate error file if needed
                with open("company_errors.txt", "a") as err_file:
                    err_file.write(f"Failed: {nextPage} with status {response.status_code}\n")
                break

            # Collect all link tags from the page
            job_links = [header.find("a", href=True) for header in soup.select("div.article__header__text") if header.find("a", href=True)]

            # Process the batch of jobs concurrently (Max 5 at a time), 10 resulted in too many 403/406 errors
            with ThreadPoolExecutor(max_workers=5) as executor:
                # map() sends each link to the process_single_job function
                batch_results = list(executor.map(lambda l: process_single_job(l, full_path, headers), job_links))

            # Filter out None results (failed pages)
            job_matches = [r for r in batch_results if r is not None]

            # Write the job_matches to a file
            if job_matches:
                with csv_lock:
                    print(f"Writing {len(job_matches)} jobs to file | {_id}")
                    with open(job_filename, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=job_fields)
                        writer.writerows(job_matches)


            # ---- find next page ----
            nextPage = None

            # Target ONLY the specific "Next" button
            #next_button = soup.select_one('a.paginationNextLink')
            #Try finding by class first
            next_button = soup.select_one('a.paginationNextLink, a[rel="next"]')

            #Fallback: If not found by class, look for "Next" in attributes (for arrow icons)
            if not next_button:
                next_button = soup.find('a', attrs={
                    "aria-label": lambda l: l and "next" in l.lower(),
                    "title": lambda t: t and "next" in t.lower()
                })

            if next_button and next_button.get("href"):
                href = next_button["href"]

                # normalize relative URLs manually
                if href.startswith("/"):
                    href = full_path.rstrip("/") + href
                elif not href.startswith("http"):
                    href = full_path.rstrip("/") + "/" + href

                if href not in visited_pages:
                    nextPage = href