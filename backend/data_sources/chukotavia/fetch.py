"""Download only linked public source documents; never run at application startup.

Usage: python -m backend.data_sources.chukotavia.fetch
Raw PDFs/HTML and the download manifest are ignored by Git. Rebuilding requires
lxml in a separate data-maintenance environment; the API needs no scraper/OCR.
"""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.href=None; self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.href=dict(attrs).get('href'); self.parts=[]
    def handle_data(self, data):
        if self.href:self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.links.append((self.href,' '.join(''.join(self.parts).split())));self.href=None


def get(url):
    if urlsplit(url).hostname != 'chukotavia.ru':
        raise ValueError('Only public chukotavia.ru documents are downloaded')
    safe=quote(url,safe=':/%?=&')
    with urlopen(Request(safe,headers={'User-Agent':'Mozilla/5.0'}),timeout=60) as response:
        data=response.read(20_000_001)
    if len(data)>20_000_000:raise ValueError('Source larger than 20 MB')
    return data


def links(data):
    p=Links();p.feed(data.decode('utf-8'));return p.links


def main():
    output=Path(__file__).resolve().parents[2]/'data/chukotavia'
    raw=output/'raw';raw.mkdir(parents=True,exist_ok=True)
    base='https://chukotavia.ru/';items=[]
    words=['расписание движения','тарифы на почтовые','тарифы на грузовые','прейскурант',
           'изменения в прейскурант','пассажиров, багажа','перевозка грузов',
           'график движения вс ми-8','план-график открытия']
    for href,title in links(get(base)):
        url=urljoin(base,href)
        if not any(w in title.lower() for w in words) or any(v['url']==url for v in items):continue
        content=get(url);identifier=url.rstrip('/').split('/')[-1]
        (raw/f'page-{identifier}.html').write_bytes(content);files=[]
        for href2,title2 in links(content):
            file_url=urljoin(url,href2)
            suffix=Path(urlsplit(file_url).path).suffix.lower()
            if suffix not in ['.pdf','.doc','.docx','.xls','.xlsx'] or any(f['url']==file_url for f in files):continue
            data=get(file_url);path=raw/(identifier+'-'+str(len(files))+suffix);path.write_bytes(data)
            files.append({'url':file_url,'title':title2,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
        items.append({'id':identifier,'title':title,'url':url,'files':files})
        print(identifier,title)
        (output/'pages.json').write_text(json.dumps(items,ensure_ascii=False,indent=2)+'\n')
    print('Downloaded',len(items),'pages. Review before rebuilding snapshot; no live tariff replacement.')


if __name__=='__main__':main()
