"""Quick scan of last 50 emails for LinkedIn/Otta/Wellfound senders."""
import imaplib
import config

mail = imaplib.IMAP4_SSL('imap.gmail.com')
mail.login(config.GMAIL_EMAIL, config.GMAIL_APP_PASSWORD)
mail.select('inbox')
status, messages = mail.search(None, 'ALL')
ids = messages[0].split()
print(f'Total emails in inbox: {len(ids)}')

valid = ['linkedin', 'otta', 'wellfound', 'angellist']
found = 0
# Check last 100 emails
for eid in ids[-100:]:
    status, data = mail.fetch(eid, '(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])')
    if status == 'OK' and data[0]:
        hdr = data[0][1].decode('utf-8', errors='ignore').lower()
        if any(v in hdr for v in valid):
            found += 1
            print(f'--- MATCH #{found} ---')
            print(data[0][1].decode('utf-8', errors='ignore').strip())

print(f'\nFound: {found} matching emails in last 100')
mail.logout()
