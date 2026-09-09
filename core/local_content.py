"""Local-only, evidence-preserving text composition. No network or AI fallback."""
import re
import string
import unicodedata

SOURCES=('sku_pattern','sku_material','sku_color','product_title','product_details','existing','video_titles')
FIELDS=('title','caption','tags','opening','cta','cover')
KINDS=('pattern','material','color','keyword','text','music_rule')
# Conservative language checks; these are risk checks, not a publishing guarantee.
NEGATIVE=re.compile(r'不支持|不具备|不防|没有|无|非|不含|不带|不适用|not\b|no\b|without\b|non[- ]|ไม่|ปราศจาก',re.I)
CLAIMS=re.compile(r'磁吸|防水|防摔|防震|适配|优惠|销量|授权|magnetic|magsafe|waterproof|shockproof|drop.?proof|compatible|discount|licensed|แม่เหล็ก|กันน้ำ|กันกระแทก',re.I)

def norm(s):return unicodedata.normalize('NFKC',str(s)).strip().casefold()
def tags(s):return list(dict.fromkeys(norm(x) for x in (re.split(r'[,，;\s]+',s) if isinstance(s,str) else s) if str(x).strip()))
def matches(text,alias):
    a=norm(alias);t=norm(text)
    return bool(a and re.search((r'(?<![\w])'+re.escape(a)+r'(?![\w])') if a.isascii() else re.escape(a),t))

def validate(record):
    r=dict(record)
    if r.get('kind') not in KINDS:raise ValueError('资料类型无效')
    for key in ('key','source'):
        if not isinstance(r.get(key),str) or not r[key].strip():raise ValueError('匹配键、来源必须填写')
        r[key]=r[key].strip()
    if len(str(r))>30000:raise ValueError('单条资料过长')
    r['aliases']=list(dict.fromkeys(norm(x) for x in (re.split(r'[,，\n]+',r.get('aliases','')) if isinstance(r.get('aliases',[]),str) else r.get('aliases',[])) if norm(x)));r['tags']=tags(r.get('tags',[]))
    if r['kind']=='text':
        if r.get('language') not in ('th','en'):raise ValueError('当前支持泰文或英文')
        if not isinstance(r.get('body'),dict) or set(r['body'])-set(FIELDS):raise ValueError('文本用途无效')
        if not all(isinstance(v,str) for v in r['body'].values()) or not any(r['body'].values()):raise ValueError('文本不能为空')
        r['generic']=bool(r.get('generic'));r['requires']=[norm(x) for x in (re.split(r'[,，\n]+',r.get('requires','')) if isinstance(r.get('requires',[]),str) else r.get('requires',[])) if norm(x)]
        for text in r['body'].values():
            try:parts=list(string.Formatter().parse(text))
            except ValueError:raise ValueError('文本占位符格式无效') from None
            for _,field,spec,conversion in parts:
                if field is not None and (r['generic'] or field not in ('keywords','pattern','material','color','category','hashtags') or spec or conversion):raise ValueError('仅针对性模板可使用 {keywords}/{pattern}/{material}/{color}/{category}')
            if r['generic'] and CLAIMS.search(text):raise ValueError('通用文案不能包含商品功能或促销断言')
            if not r['generic'] and CLAIMS.search(text) and not r['requires']:raise ValueError('功能型文字必须填写所需事实关键词匹配键')
    elif r['kind']=='music_rule':
        r['language']='';r['match_tags']=tags(r.get('match_tags',[]));r['music_tags']=tags(r.get('music_tags',[]))
        if not r['match_tags'] or not r['music_tags']:raise ValueError('音乐匹配规则需要输入标签和音乐标签')
    else:
        r['language']='';r['property']=r['kind'] if r['kind']!='keyword' else r.get('property','pattern')
        if r['property'] not in ('pattern','material','color','feature','category','model','price','promotion'):raise ValueError('关键词属性无效')
        r['en']=str(r.get('en','')).strip();r['th']=str(r.get('th','')).strip()
        if not r['en'] and not r['th']:raise ValueError('至少需要一项已确认泰文或英文表达')
        if r['property'] in ('pattern','material','color') and CLAIMS.search(r['en']+' '+r['th']):raise ValueError('图名、材质、颜色词不能附加功能断言；请作为独立事实关键词管理')
        if r.get('confirmed') is not True:raise ValueError('请确认表达已有资料依据，不是推测')
    r['category']=str(r.get('category','')).strip()
    return r

def builtins():
    result=[]
    bodies={
      'en':[
       ['A closer look','Take a closer look at the details.','#CloserLook','Let’s take a look.','Explore the details.','A closer look'],
       ['Details in focus','See the details from another angle.','#DetailsInFocus','Here are the details.','Which detail catches your eye?','Details in focus']],
      'th':[
       ['มาดูรายละเอียดกัน','ชมรายละเอียดให้ใกล้ขึ้น','#ชมรายละเอียด','มาดูกันใกล้ ๆ','ดูรายละเอียดเพิ่มเติม','ชมรายละเอียด'],
       ['รายละเอียดในอีกมุม','ลองชมรายละเอียดจากอีกมุมหนึ่ง','#ดูอีกมุม','มาดูอีกมุมกัน','คุณชอบรายละเอียดตรงไหน','มองอีกมุม']]
    }
    for language,sets in bodies.items():
        for i,body in enumerate(sets):result.append(dict(id=f'builtin_generic_{language}_{i}',kind='text',key=f'通用 {language} {i+1}',language=language,generic=True,body=dict(zip(FIELDS,body)),source='软件内置通用文本；不含商品功能声明'))
        body=dict(zip(FIELDS,sets[0]));body['title']='{keywords}';body['tags']='{hashtags}';body['caption']=('A closer look: ' if language=='en' else 'ชมรายละเอียด: ')+'{keywords}'
        result.append(dict(id=f'builtin_targeted_{language}',kind='text',key=f'关键词文本 {language}',language=language,generic=False,body=body,source='软件内置中性模板；属性仅来自本次已确认关键词'))
    return result

def allocate(pool,order,index,rng,state,key):
    if order!='random':return pool[index%len(pool)]
    bag=state.setdefault((key,tuple(pool)),{'remaining':[],'last':None})
    if not bag['remaining']:
        bag['remaining']=list(pool);rng.shuffle(bag['remaining'])
        if len(pool)>1 and bag['remaining'][-1]==bag['last']:bag['remaining'][0],bag['remaining'][-1]=bag['remaining'][-1],bag['remaining'][0]
    bag['last']=bag['remaining'].pop()
    return bag['last']

def compose(records,products,skus,assets,options,video_titles,index,rng,rotation=None):
    language=options.get('language','th')
    if language not in ('th','en'):raise ValueError('文案语言无效')
    sources=options.get('sources',[])
    if not isinstance(sources,list) or set(sources)-set(SOURCES):raise ValueError('关键词来源无效')
    if options.get('mode','local')=='ai':raise ValueError('AI文案等待接入；没有发送请求')
    if options.get('order','cycle') not in ('cycle','random'):raise ValueError('文本分配顺序无效')
    keywords=[r for r in records if r['kind'] in ('pattern','material','color','keyword')]
    trace={'mode':'local','language':language,'sources':sources,'keywords':[],'unmatched':[],'omitted':[],'templates':{},'fallback':{},'sku':[]}
    # Contexts represent every actual product/SKU, so collections use only shared statements.
    contexts=[]
    for p in products:
        relevant=[s for s in skus if s['product_id']==p['id']] or [None]
        contexts.extend((p,s) for s in relevant)
    if not contexts:contexts=[(None,None)]
    selected_ids=options.get('keyword_ids',[])
    if any(i not in {r['id'] for r in keywords} for i in selected_ids):raise ValueError('所选关键词已删除或不存在')
    common=None
    for p,sku in contexts:
        candidates={};blocked=set()
        def add(r,source,evidence,negative=False,usable=True):
            item={'id':r['id'],'key':r['key'],'property':r['property'],'source':source,'evidence':evidence,'product_id':p['id'] if p else None,'sku_id':sku['id'] if sku else None,'record':r,'negative':negative,'usable':usable}
            trace['keywords'].append(item)
            if negative:blocked.add(r['id'])
            elif usable:candidates[r['id']]=r
        if sku:
            parts=re.split(r'\s*[-‐‑–—]\s*',sku['name'])
            parsed=dict(zip(('pattern','material','color'),parts)) if len(parts)==3 and all(parts) else {}
            trace['sku'].append({'id':sku['id'],'raw':sku['name'],'parsed':parsed})
            if not parsed:trace['unmatched'].append('SKU无法明确拆分：'+sku['name'])
            for kind,key in parsed.items():
                if 'sku_'+kind not in sources:continue
                found=[r for r in keywords if r['kind']==kind and norm(key) in [norm(r['key']),*r['aliases']]]
                if len(found)==1:add(found[0],'sku_'+kind,key)
                else:trace['unmatched'].append(kind+'未唯一匹配：'+key)
        for field,source in (('title','product_title'),('details','product_details')):
            if not p:continue
            # Unselected sources may veto a claim, but never contribute text.
            for sentence in re.split(r'[。.!?\n;；]+',p.get(field,'')):
                for r in keywords:
                    if any(matches(sentence,a) for a in [r['key'],*r['aliases']]):
                        negative=bool(NEGATIVE.search(sentence))
                        if negative:blocked.add(r['id']);trace['omitted'].append('否定或不明确证据：'+sentence)
                        elif source in sources:add(r,source,sentence)
        if 'existing' in sources:
            for r in keywords:
                selected=r['id'] in selected_ids
                filename=r['kind']=='pattern' and any(norm(a['name'].rsplit('.',1)[0]) in [norm(r['key']),*r['aliases']] for a in assets)
                prior=p and any(matches(p.get('keywords',''),a) for a in [r['key'],*r['aliases']])
                if selected or filename or prior:
                    if r['property'] in ('feature','model','price','promotion'):
                        trace['omitted'].append('事实关键词需要本次商品标题/详情证据：'+r['key'])
                    else:add(r,'existing',r['source'])
        if 'video_titles' in sources:
            for title in video_titles:
                for r in keywords:
                    if any(matches(title,a) for a in [r['key'],*r['aliases']]):add(r,'video_titles',title,usable=False)
            trace['omitted'].append('已有视频标题仅整理和检索，不作为商品事实扩写')
        # Conflicting scalar attributes are omitted, never arbitrarily picked.
        for prop in ('material','color','model','price','promotion','category'):
            rows=[r for r in candidates.values() if r['property']==prop]
            if len({r['key'] for r in rows})>1:
                blocked.update(r['id'] for r in rows);trace['omitted'].append('属性冲突，已省略：'+prop)
        ids=set(candidates)-blocked
        common=ids if common is None else common&ids
    used=[r for r in keywords if r['id'] in (common or set()) and r.get(language)]
    for item in trace['keywords']:item['used']=item['id'] in {r['id'] for r in used} and item['usable'] and not item['negative']
    if len(contexts)>1:trace['omitted'].append('合集仅使用各实际商品/SKU共有且无冲突的关键词')
    values={prop:' · '.join(dict.fromkeys(r[language] for r in used if r['property']==prop)) for prop in ('pattern','material','color','category')}
    values['keywords']=' · '.join(dict.fromkeys(r[language] for r in used))
    values['hashtags']=' '.join('#'+re.sub(r'[\s#.,;:!?{}·]+','',r[language]) for r in used)
    category=products[0].get('category','') if products and all(p.get('category')==products[0].get('category') for p in products) else ''
    pool=options.get('text_ids',[])
    texts=[r for r in records if r['kind']=='text' and r['language']==language and (not r.get('category') or norm(r['category'])==norm(category))]
    if pool and any(i not in {r['id'] for r in records if r['kind']=='text' and r['language']==language} for i in pool):raise ValueError('文本池包含其他语言、已删除或不存在的记录')
    output={};keys={norm(r['key']) for r in used}
    for field in FIELDS:
        suitable=[]
        for r in texts:
            body=r['body'].get(field,'')
            if not body or r.get('generic') or (pool and r['id'] not in pool) or not used or not set(r.get('requires',[]))<=keys:continue
            fields=[f for _,f,_,_ in string.Formatter().parse(body) if f is not None]
            if all(values.get(f) for f in fields):suitable.append(r)
        fallback=not suitable
        if fallback:
            suitable=[r for r in texts if r.get('generic') and r['body'].get(field)]
            preferred=[r for r in suitable if r['id'] in pool]
            if preferred:suitable=preferred
        if not suitable:raise ValueError(f'{language} 的 {field} 没有可用通用文本，请在本地资料库补齐')
        chosen=allocate([r['id'] for r in suitable],options.get('order','cycle'),index,rng,rotation if rotation is not None else {},field)
        r=next(r for r in suitable if r['id']==chosen)
        output[field]=r['body'][field].format_map(values)
        if '{' in output[field] or '}' in output[field]:raise ValueError('文本含未替换占位符')
        trace['templates'][field]=r;trace['fallback'][field]=fallback
    trace['tags']=list(dict.fromkeys(t for r in used for t in r.get('tags',[])))
    return output,trace

def match_music(records,rows,keyword_tags,template_tags):
    inputs=set(tags(keyword_tags)+tags(template_tags));wanted=set(inputs);rules=[]
    for r in records:
        if r['kind']=='music_rule' and inputs.intersection(r['match_tags']):wanted.update(r['music_tags']);rules.append(r)
    scored=[]
    for row in rows:
        overlap=wanted.intersection(tags(row['metadata'].get('tags',[])))
        if overlap:scored.append((len(overlap),row['id'],sorted(overlap)))
    best=max((s[0] for s in scored),default=0)
    return [s[1] for s in scored if s[0]==best],{'status':'matched' if best else 'unmatched','input_tags':sorted(inputs),'rules':rules,'matches':[{'Music_ID':s[1],'tags':s[2]} for s in scored if s[0]==best]}
