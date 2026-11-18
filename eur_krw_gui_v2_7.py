# Windows trust store integration: pip install truststore
try:
    import truststore
    truststore.inject_into_ssl()
    TRUSTSTORE_READY = True
except ImportError:
    TRUSTSTORE_READY = False
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import StringIO
import re
import requests
import urllib3
from pathlib import Path
from playwright.sync_api import sync_playwright
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import os
import certifi
import ssl
import urllib.request
import pandas as pd
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from bs4 import BeautifulSoup

TIMEOUT = 10
# Optional enterprise CA bundle: set REQUESTS_CA_BUNDLE or SSL_CERT_FILE.
CA_BUNDLE = False  # Requested workaround: TLS certificate verification disabled for requests
# requests uses OS trust when truststore is installed and injected. Otherwise certifi applies.

HEADERS = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36','Accept-Language':'en-US,en;q=0.9'}
executor = ThreadPoolExecutor(max_workers=2)

def get(url, **kwargs):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=CA_BUNDLE, **kwargs)
    r.raise_for_status()
    return r

def _read_google_pair(page, pair, diagnostic_dir):
    url = f'https://www.google.com/finance/quote/{pair}?hl=en'
    response = page.goto(url, wait_until='domcontentloaded', timeout=35000)
    page.wait_for_timeout(1800)
    if response is None or response.status >= 400:
        raise RuntimeError(f'{pair}: HTTP {response.status if response else "no response"}')
    body = page.locator('body').inner_text(timeout=12000)
    if pair.replace('-', ' / ').lower() not in body.lower():
        raise ValueError(f'{pair}: 통화쌍 제목 미확인')
    for selector in ('div.YMlKec.fxKbKc', 'div.YMlKec', '[data-last-price]'):
        loc = page.locator(selector)
        for idx in range(min(loc.count(), 5)):
            node = loc.nth(idx)
            raw = (node.get_attribute('data-last-price') or node.inner_text()).strip().replace(',', '')
            if re.fullmatch(r'\d+(?:\.\d+)?', raw):
                value = float(raw)
                if _valid_google_rate(pair, value):
                    return value, selector
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if line.upper().replace(' ', '') == pair.replace('-', '/').upper():
            for candidate in lines[idx + 1:idx + 9]:
                if re.fullmatch(r'[\d,]+(?:\.\d+)?', candidate):
                    value = float(candidate.replace(',', ''))
                    if _valid_google_rate(pair, value):
                        return value, 'heading-neighbor'
    raise ValueError(f'{pair}: 화면에서 환율 숫자 식별 실패')


def _valid_google_rate(pair, value):
    return (0.3 < value < 2.0) if pair == 'USD-EUR' else (500 < value < 3000)


def google_rates_browser():
    """Fetch both pairs in one Chrome/Edge session; return partial results and errors."""
    diagnostic_dir = Path.home() / 'google_finance_diagnostics'
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    values, errors = {}, []
    with sync_playwright() as playwright:
        browser = None
        for channel in ('chrome', 'msedge'):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=False)
                break
            except Exception as exc:
                errors.append(f'{channel} 실행: {exc}')
        if browser is None:
            raise RuntimeError('설치된 Chrome/Edge 실행 실패: ' + ' | '.join(errors))
        try:
            context = browser.new_context(locale='en-US', ignore_https_errors=True)
            page = context.new_page()
            for pair, key in (('USD-EUR', 'usd_eur'), ('USD-KRW', 'usd_krw')):
                try:
                    value, method = _read_google_pair(page, pair, diagnostic_dir)
                    values[key] = (value, f'Google Finance / {method} / {datetime.now():%Y-%m-%d %H:%M:%S}')
                except Exception as exc:
                    errors.append(f'{pair}: {exc}')
                    try:
                        page.screenshot(path=str(diagnostic_dir / f'{pair}.png'), full_page=False)
                        (diagnostic_dir / f'{pair}.html').write_text(page.content(), encoding='utf-8')
                    except Exception:
                        pass
        finally:
            browser.close()
    return values, errors

def history():
    url='https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/krw.xml'
    import xml.etree.ElementTree as ET
    root=ET.fromstring(get(url).content)
    rows=[]
    for el in root.iter():
        if el.tag.rsplit('}',1)[-1]=='Obs':
            date=el.get('TIME_PERIOD'); value=el.get('OBS_VALUE')
            if date and value: rows.append((date,float(value)))
    if len(rows)<60: raise ValueError('ECB 과거 환율 관측치가 60개 미만입니다.')
    df=pd.DataFrame(rows,columns=['Date','EUR/KRW'])
    df['Date']=pd.to_datetime(df['Date'])
    return df.set_index('Date').sort_index().loc[lambda x:~x.index.duplicated(keep='last')]

def _parse_fed_official_csv(content):
    """Fed historical target range CSV: dates across columns, lower/upper in rows."""
    df = pd.read_csv(StringIO(content), dtype=str)
    if len(df) < 2:
        raise ValueError('Fed CSV에는 목표금리 하단/상단 두 행이 필요합니다.')
    candidates = []
    for col in df.columns:
        date = pd.to_datetime(col, errors='coerce', utc=True)
        if pd.isna(date):
            continue
        try:
            lower = float(str(df.iloc[0][col]).strip().replace(',', ''))
            upper = float(str(df.iloc[1][col]).strip().replace(',', ''))
        except (ValueError, TypeError):
            continue
        if not (0 <= lower <= upper <= 30):
            continue
        candidates.append((date, lower, upper))
    if not candidates:
        raise ValueError('Fed CSV에서 날짜별 목표금리 상단을 추출하지 못했습니다.')
    date, lower, upper = max(candidates, key=lambda item: item[0])
    return upper, f'Federal Reserve 공식 목표금리 CSV (상단; {date:%Y-%m-%d}; 범위 {lower:.2f}~{upper:.2f}%)'


def fed_rate():
    """Use Federal Reserve CSV; browser download fallback if requests is blocked."""
    url = 'https://www.federalreserve.gov/aboutthefed/files/target-funds-2014-2024.csv'
    errors = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=(5, 15), verify=CA_BUNDLE)
        r.raise_for_status()
        return _parse_fed_official_csv(r.content.decode('utf-8-sig'))
    except Exception as exc:
        errors.append(f'requests: {exc}')
    try:
        with sync_playwright() as playwright:
            browser = None
            for channel in ('chrome', 'msedge'):
                try:
                    browser = playwright.chromium.launch(channel=channel, headless=False)
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError('Chrome/Edge 실행 불가')
            try:
                context = browser.new_context(ignore_https_errors=True, accept_downloads=True)
                page = context.new_page()
                with page.expect_download(timeout=30000) as download_info:
                    page.goto(url, wait_until='commit', timeout=30000)
                download = download_info.value
                from tempfile import TemporaryDirectory
                with TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'fed.csv'
                    download.save_as(str(path))
                    return _parse_fed_official_csv(path.read_text(encoding='utf-8-sig'))
            finally:
                browser.close()
    except Exception as exc:
        errors.append(f'Chrome/Edge: {exc}')
    raise RuntimeError('Fed 공식 CSV 자동 조회 실패: ' + ' | '.join(errors))

def bok_rate():
    soup=BeautifulSoup(get('https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643').text,'html.parser')
    for table in soup.select('table'):
        for row in table.select('tbody tr'):
            cols=[x.get_text(' ',strip=True) for x in row.select('td')]
            if len(cols)>=3:
                for candidate in (cols[2],cols[-1]):
                    m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*%?\s*',candidate)
                    if m:
                        value=float(m.group(1))
                        if 0<=value<=20:return value,'한국은행 기준금리 공시'
    raise ValueError('한국은행 표 구조를 확인할 수 없습니다.')

def ecb_rate():
    # ECB Data API, deposit facility rate (DFR); policy-rate definition explicit.
    url='https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR.LEV?lastNObservations=5'
    r=get(url,headers={'Accept':'text/csv'}) if False else requests.get(url,headers={**HEADERS,'Accept':'text/csv'},timeout=TIMEOUT,verify=CA_BUNDLE)
    r.raise_for_status()
    df=pd.read_csv(StringIO(r.text))
    if 'OBS_VALUE' not in df: raise ValueError('ECB CSV OBS_VALUE 열 없음')
    s=pd.to_numeric(df['OBS_VALUE'],errors='coerce').dropna()
    if s.empty: raise ValueError('ECB DFR 자료 없음')
    return float(s.iloc[-1]),'ECB DFR (예금금리)'

def compute(usd_eur,usd_krw,fed,bok,ecb,df,bp):
    if min(usd_eur,usd_krw)<=0:raise ValueError('환율은 0보다 커야 합니다.')
    if bp<0 or bp>10000:raise ValueError('bp는 0~10000 범위여야 합니다.')
    df=df.copy()
    for n in (5,20,60):df[f'MA{n}']=df['EUR/KRW'].rolling(n).mean()
    spot=usd_krw/usd_eur
    usdkrw_fwd=usd_krw*(1+bok/100)/(1+fed/100)
    usdeur_fwd=usd_eur*(1+ecb/100)/(1+fed/100)
    fwd=usdkrw_fwd/usdeur_fwd
    ma5,ma20,ma60=(float(df[f'MA{n}'].iloc[-1]) for n in (5,20,60))
    last=float(df['EUR/KRW'].iloc[-1]); recent=df['EUR/KRW'].tail(60)
    lo=float(recent.min()); hi=float(recent.max())
    gap=(ma5-ma20)/ma20*100
    if last>ma5>ma20>ma60 and gap>0.5:trend='급등 추세'
    elif last<ma5<ma20<ma60 and gap< -0.5:trend='급락 추세'
    elif last>ma5 and last>ma20:trend='상승 추세'
    elif last<ma5 and last<ma20 and last<ma60:trend='하락 추세'
    else:trend='조정 국면'
    dev_ma=(spot-ma20)/ma20*100
    premium=(fwd/spot-1)*100
    if gap>=1: momentum='단기 과열 신호'
    elif gap>=.5:momentum='상승 모멘텀 강화'
    elif gap<=-1:momentum='단기 급락 신호'
    elif gap<=-.5:momentum='하락 모멘텀 강화'
    else:momentum='단기 모멘텀 중립'
    if dev_ma>=1: ma_note='20일선 대비 +1% 이상'
    elif dev_ma>=.5:ma_note='20일선 대비 상승 이격'
    elif dev_ma<=-1:ma_note='20일선 대비 -1% 이하'
    elif dev_ma<=-.5:ma_note='20일선 대비 하락 이격'
    else:ma_note='20일선 부근'
    return locals()

class App:
    def __init__(self,root):
        self.root=root; root.title('EUR/KRW 환율 분석기 | 2.7 (Google Finance)');root.geometry('1050x810');root.minsize(850,660)
        self.vars={k:tk.StringVar(value=v) for k,v in {'usd_eur':'','usd_krw':'','fed':'','bok':'','ecb':'','bp':'30'}.items()}
        self.status=tk.StringVar(value='Google Finance Chrome/Edge 조회 준비 | SSL 검증 우회 활성화')
        top=ttk.LabelFrame(root,text='시장 데이터 입력 (수동 수정 가능)',padding=10);top.pack(fill='x',padx=12,pady=8)
        labels=[('USD/EUR','usd_eur'),('USD/KRW','usd_krw'),('Fed 상단 %','fed'),('BOK %','bok'),('ECB DFR %','ecb'),('변동폭 ±bp','bp')]
        for i,(label,key) in enumerate(labels):
            ttk.Label(top,text=label).grid(row=i//3*2,column=i%3,sticky='w',padx=8)
            ttk.Entry(top,textvariable=self.vars[key],width=22).grid(row=i//3*2+1,column=i%3,sticky='ew',padx=8,pady=(0,8))
            top.columnconfigure(i%3,weight=1)
        controls=ttk.Frame(root);controls.pack(fill='x',padx=12)
        self.fetchbtn=ttk.Button(controls,text='환율·금리 자동 조회 (Google Finance)',command=self.fetch_all);self.fetchbtn.pack(side='left',padx=(0,8))
        ttk.Button(controls,text='접속 환경 진단',command=self.diagnose).pack(side='left',padx=(0,8))
        self.analyzebtn=ttk.Button(controls,text='분석 실행 / 그래프 갱신',command=self.analyze);self.analyzebtn.pack(side='left',padx=(0,8))
        ttk.Button(controls,text='과거 환율 CSV 불러오기',command=self.load_csv).pack(side='left')
        ttk.Label(controls,textvariable=self.status).pack(side='left',padx=15)
        notebook=ttk.Notebook(root);notebook.pack(fill='both',expand=True,padx=12,pady=10)
        text_tab=ttk.Frame(notebook);graph_tab=ttk.Frame(notebook)
        notebook.add(text_tab,text='분석 보고서');notebook.add(graph_tab,text='추세 그래프')
        self.text=scrolledtext.ScrolledText(text_tab,wrap='word',font=('Malgun Gothic',11));self.text.pack(fill='both',expand=True)
        self.figure=Figure(figsize=(10,5),dpi=100);self.ax=self.figure.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.figure,master=graph_tab);self.canvas.get_tk_widget().pack(fill='both',expand=True)
        self.cached_history=None;self.history_source='ECB 온라인 XML';self.sources={}
    def diagnose(self):
        self.busy(True,'접속 환경 진단 중...')
        def task():
            urls={
                'Google Finance':'https://www.google.com/finance/quote/USD-KRW',
                'ExchangeRate API':'https://open.er-api.com/v6/latest/USD',
                'ECB XML':'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/krw.xml',
                'Fed 공식 CSV':'https://www.federalreserve.gov/aboutthefed/files/target-funds-2014-2024.csv',
                'BOK':'https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643'
            }
            lines=[f'진단시각: {datetime.now().isoformat(timespec="seconds")}',
                   f'truststore 활성화: {TRUSTSTORE_READY}',
                   f'HTTP requests SSL verify: {CA_BUNDLE} (인증서 검증 비활성화)',
                   f'환경 프록시: {requests.utils.get_environ_proxies("https://www.google.com")}',
                   f'Windows/Python 프록시: {urllib.request.getproxies()}', '']
            for name,url in urls.items():
                try:
                    r=requests.get(url,headers=HEADERS,timeout=(4,7),verify=CA_BUNDLE)
                    lines.append(f'{name}: HTTP {r.status_code}, {len(r.content)} bytes, content-type={r.headers.get("Content-Type","")}')
                except Exception as exc:
                    lines.append(f'{name}: {type(exc).__name__}: {exc}')
            return '\n'.join(lines)
        def done(result):
            from pathlib import Path
            path=Path.home()/'eur_krw_connection_diagnostics.txt'
            path.write_text(result,encoding='utf-8')
            self.text.delete('1.0','end');self.text.insert('end',result)
            self.busy(False,f'접속 진단 완료: {path}')
        self.job(task,done)
    def load_csv(self):
        path=filedialog.askopenfilename(title='ECB 과거 EUR/KRW CSV 선택',filetypes=[('CSV files','*.csv'),('All files','*.*')])
        if not path:return
        try:
            raw=pd.read_csv(path,encoding='utf-8-sig',sep=None,engine='python')
            raw.columns=[str(c).strip() for c in raw.columns]
            datecol=next((c for c in raw if c.lower() in ('date','time_period','날짜','일자')),None)
            ratecol=next((c for c in raw if c.lower() in ('eur/krw','obs_value','rate','환율')),None)
            if datecol is None or ratecol is None:
                raise ValueError('CSV에 Date 및 EUR/KRW 열(또는 TIME_PERIOD 및 OBS_VALUE 열)이 필요합니다.')
            df=pd.DataFrame({'Date':pd.to_datetime(raw[datecol],errors='coerce'),
                             'EUR/KRW':pd.to_numeric(raw[ratecol],errors='coerce')}).dropna()
            df=df.set_index('Date').sort_index()
            df=df.loc[~df.index.duplicated(keep='last')]
            if len(df)<60:raise ValueError('최소 60개 날짜의 환율 자료가 필요합니다.')
            if (df['EUR/KRW']<=0).any():raise ValueError('환율은 양수여야 합니다.')
            self.cached_history=df
            self.history_source='로컬 CSV: '+path
            self.status.set(f'CSV 로드 완료: {len(df)}건, 마지막 {df.index[-1]:%Y-%m-%d}')
        except Exception as exc:
            messagebox.showerror('CSV 읽기 실패',str(exc))

    def busy(self,value,msg):
        self.fetchbtn.config(state='disabled' if value else 'normal');self.analyzebtn.config(state='disabled' if value else 'normal');self.status.set(msg)
    def job(self,func,done):
        future=executor.submit(func)
        def poll():
            if not future.done():self.root.after(120,poll);return
            try: result=future.result();done(result)
            except Exception as e:self.busy(False,'작업 실패');messagebox.showerror('조회/분석 오류',str(e))
        self.root.after(120,poll)
    def fetch_all(self):
        self.busy(True,'데이터 조회 중...')
        def task():
            results={};errors=[]
            try:
                fx_values, fx_errors = google_rates_browser()
                for key, (value, source) in fx_values.items():
                    results[key] = value
                    results[key+'_source'] = source
                errors.extend(fx_errors)
            except Exception as exc:
                errors.append(f'Google Finance: {exc}')
            for key,fn in [('fed',fed_rate),('bok',bok_rate),('ecb',ecb_rate)]:
                try:
                    value=fn()
                    if isinstance(value,tuple):results[key]=value[0];results[key+'_source']=value[1]
                    else:results[key]=value;results[key+'_source']='출처 미확인'
                except Exception as exc:errors.append(f'{key}: {exc}')
            return results,errors
        def done(result):
            values,errors=result
            for k,v in values.items():
                if k.endswith('_source'):
                    self.sources[k[:-7]]=v
                else:
                    self.vars[k].set(f'{v:.5f}' if k.startswith('usd') else f'{v:.3f}')
            self.busy(False,'조회 완료 (Google Finance 화면값; 실시간 체결호가 아님)' if not errors else f'일부 조회 실패 ({len(errors)}건) — 수동 입력 가능')
            if errors:
                from pathlib import Path
                log=Path.home()/'eur_krw_network_errors.txt'
                log.write_text('\n\n'.join(errors),encoding='utf-8')
                summary='\n'.join(f'{e.split(":",1)[0]}: 조회 실패' for e in errors)
                self.status.set('일부 조회 실패: '+summary.replace('\n',', ')+' — 수동 입력 후 분석 가능')
        self.job(task,done)
    def analyze(self):
        try:
            values={k:float(v.get().replace(',','')) for k,v in self.vars.items()}
        except ValueError:
            messagebox.showerror('입력 오류','모든 환율·금리·bp 입력칸에 숫자를 입력하십시오.');return
        self.busy(True,'과거 환율 자료 확인 및 분석 중...')
        def task():
            if self.cached_history is not None:
                df=self.cached_history
            else:
                try:df=history()
                except Exception as exc:
                    raise RuntimeError('ECB 과거 환율 온라인 조회 실패. [과거 환율 CSV 불러오기]로 Date,EUR/KRW 형식의 CSV를 지정하십시오.\n원인: '+str(exc)) from exc
            return df,compute(values['usd_eur'],values['usd_krw'],values['fed'],values['bok'],values['ecb'],df,values['bp'])
        def done(result):
            df,a=result;self.cached_history=df
            spot=a['spot'];fwd=a['fwd'];bp=values['bp'];lo=a['lo'];hi=a['hi']
            report=f'''EUR/KRW 환율 분석 보고서\n분석 시각: {datetime.now():%Y-%m-%d %H:%M:%S} (PC 현지시간)\nECB/CSV 마지막 관측일: {df.index[-1]:%Y-%m-%d}\n시계열 출처: {self.history_source}\n\n[1. 입력 시장 데이터]\nUSD/EUR: {values['usd_eur']:.5f} | USD/KRW: {values['usd_krw']:,.3f}\nFed 목표금리 상단: {values['fed']:.3f}% | BOK: {values['bok']:.3f}% | ECB 예금금리: {values['ecb']:.3f}%\n\n[2. 교차환율 및 이론 선도환율]\nEUR/KRW 교차 현물환율: {spot:,.2f}원\n1년 만기 단순 금리평가 이론 선도환율: {fwd:,.2f}원\n이론 선도 프리미엄: {a['premium']:+.2f}%\n※ 이론 선도환율은 미래 현물환율 전망치 또는 적정 현물가가 아닙니다.\n※ Fed 금리는 삼각환산 과정에서 상쇄됩니다. 실제 거래 선도환율은 금리기간·스왑베이시스 등에 따라 다릅니다.\n\n[3. ECB 과거 환율 기술적 분석]\n최신 ECB 고시환율: {a['last']:,.2f}원\n추세: {a['trend']}\nMA5: {a['ma5']:,.2f} | MA20: {a['ma20']:,.2f} | MA60: {a['ma60']:,.2f}\nMA5-MA20 이격도: {a['gap']:+.2f}%\n현재 교차 현물환율의 MA20 대비 괴리: {a['dev_ma']:+.2f}%\n최근 60거래 관측치 최저/최고: {lo:,.2f} ~ {hi:,.2f}원\n시장 해석: {a['momentum']} / {a['ma_note']}\n※ 최저·최고는 관측 범위이며 확정적인 지지·저항선이 아닙니다.\n\n[4. 기계적 변동폭 시나리오]\n±10bp: {spot*.999:,.2f} ~ {spot*1.001:,.2f}원\n±20bp: {spot*.998:,.2f} ~ {spot*1.002:,.2f}원\n±{bp:g}bp: {spot*(1-bp/10000):,.2f} ~ {spot*(1+bp/10000):,.2f}원\n※ 위 구간은 입력 비율에 따른 산술적 시나리오이며 예측구간이 아닙니다.\n\n[5. 데이터 기준]\n환율은 Google Finance 브라우저 표시값 또는 수동 입력값이며 실시간 매매호가가 아닙니다.\n자동 조회 출처: {self.sources.get('usd_eur', '수동 입력')} / {self.sources.get('usd_krw', '수동 입력')}\nECB 시계열은 고시 기준이며 실시간 시장환율과 시차가 있습니다.\n기준금리는 사용자가 수정할 수 있으므로 실제 공시 최신치인지 확인하십시오.\n'''
            self.text.delete('1.0','end');self.text.insert('end',report)
            # compute() creates moving-average columns in its private copy.
            # Plot that analyzed frame rather than the raw cached ECB frame.
            analyzed_df=a['df']
            self.ax.clear();recent=analyzed_df.tail(180)
            for key,label in [('EUR/KRW','ECB EUR/KRW'),('MA5','MA5'),('MA20','MA20'),('MA60','MA60')]:self.ax.plot(recent.index,recent[key],label=label)
            self.ax.axhline(spot,linestyle='--',label='Current cross spot')
            self.ax.set_title('EUR/KRW - ECB observations and moving averages');self.ax.set_ylabel('KRW per EUR');self.ax.legend(loc='best');self.ax.grid(alpha=.25)
            self.figure.autofmt_xdate();self.figure.tight_layout();self.canvas.draw()
            self.busy(False,'분석 완료')
        self.job(task,done)

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
