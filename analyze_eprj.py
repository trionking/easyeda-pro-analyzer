#!/usr/bin/env python3
"""
EasyEDA Pro (.eprj) Schematic Analyzer v4

Key insight: EasyEDA Pro assigns NET names as ATTR on WIRE elements.
Wires with the same NET name are electrically connected even if physically separate.
This is the primary net connection mechanism, supplemented by:
- Shared coordinates (wire endpoints touching)
- T-junctions (endpoint on mid-segment)
- Multi-segment wires (same WIRE id = same net)

Usage: python3 analyze_eprj.py <file.eprj> [--json] [--sheet <name>]
"""

import sqlite3, base64, zlib, json, sys, math, re
from collections import defaultdict

def decode(ds):
    if ds.startswith("base64"): ds = ds[6:]
    return zlib.decompress(base64.b64decode(ds), 15+32).decode('utf-8')

def jlines(t):
    r = []
    for l in t.strip().split('\n'):
        try: r.append(json.loads(l.strip()))
        except: pass
    return r

def rotpt(x, y, rot, mir=0):
    if mir: x = -x
    r = rot % 360
    if r==0: return(x,y)
    if r==90: return(-y,x)
    if r==180: return(-x,-y)
    if r==270: return(y,-x)
    a=math.radians(r); return(x*math.cos(a)-y*math.sin(a), x*math.sin(a)+y*math.cos(a))

def snap(v): return round(v*2)/2

def pt_on_seg(px,py,x1,y1,x2,y2,tol=1.0):
    if not(min(x1,x2)-tol<=px<=max(x1,x2)+tol and min(y1,y2)-tol<=py<=max(y1,y2)+tol): return False
    dx,dy=x2-x1,y2-y1; s2=dx*dx+dy*dy
    if s2<.001: return math.hypot(px-x1,py-y1)<=tol
    t=max(0,min(1,((px-x1)*dx+(py-y1)*dy)/s2))
    return math.hypot(px-(x1+t*dx),py-(y1+t*dy))<=tol

class UF:
    def __init__(self): self.p={}; self.r={}
    def find(self,x):
        if x not in self.p: self.p[x]=x; self.r[x]=0
        while self.p[x]!=x: self.p[x]=self.p[self.p[x]]; x=self.p[x]
        return x
    def union(self,a,b):
        a,b=self.find(a),self.find(b)
        if a==b: return
        if self.r[a]<self.r[b]: a,b=b,a
        self.p[b]=a
        if self.r[a]==self.r[b]: self.r[a]+=1

class Analyzer:
    def __init__(self, fp):
        self.conn=sqlite3.connect(fp); self.cur=self.conn.cursor()
    def close(self): self.conn.close()

    def project(self):
        self.cur.execute("SELECT name FROM projects LIMIT 1")
        r=self.cur.fetchone(); return {"name":r[0] if r else "?"}

    def sheets(self):
        self.cur.execute("SELECT uuid,title,display_title,sheet_id FROM documents WHERE docType=1 ORDER BY sheet_id")
        return [{"uuid":r[0],"title":r[1],"display_title":r[2],"sheet_id":r[3]} for r in self.cur.fetchall()]

    def sym_pins(self, title):
        t_lower = title.lower()
        self.cur.execute("SELECT dataStr FROM components WHERE lower(title)=? AND docType=2",(t_lower,))
        r=self.cur.fetchone()
        if not r:
            # Strip .N multi-part suffix (e.g. 'DTC143ZE.1' → 'dtc143ze')
            stripped = re.sub(r'\.\d+$', '', t_lower)
            if stripped != t_lower:
                self.cur.execute("SELECT dataStr FROM components WHERE lower(title)=? AND docType=2",(stripped,))
                r=self.cur.fetchone()
        if not r or not r[0]: return {}
        try: t=decode(r[0]) if r[0].startswith("base64") else r[0]
        except: return {}
        pp,pa={},defaultdict(dict)
        for e in jlines(t):
            if e[0]=='PIN' and len(e)>=8: pp[e[1]]=(e[4],e[5],e[6],e[7])
            elif e[0]=='ATTR' and len(e)>=5: pa[e[2]][e[3]]=e[4]
        pins={}
        for pid,(px,py,pl,pr) in pp.items():
            a=pa.get(pid,{}); n=a.get('NUMBER','')
            if n: pins[n]={"name":a.get('NAME',''),"number":n,"x":px,"y":py}
        return pins

    def parse_sheet(self, title):
        self.cur.execute("SELECT dataStr FROM documents WHERE title=?",(title,))
        r=self.cur.fetchone()
        if not r or not r[0]: return {}
        elems=jlines(decode(r[0]))

        comps,wires,texts,wire_attrs={},{},{},{}
        for e in elems:
            if e[0]=='COMPONENT' and len(e)>=5:
                comps[e[1]]={'name':e[2] if len(e)>2 else '','x':e[3],'y':e[4],
                             'rot':e[5] if len(e)>5 else 0,'mir':e[6] if len(e)>6 else 0,'a':{}}
            elif e[0]=='ATTR' and len(e)>=5:
                if e[2] in comps: comps[e[2]]['a'][e[3]]=e[4]
                elif e[2] in wires: wire_attrs.setdefault(e[2],{})[e[3]]=e[4]
                else: wire_attrs.setdefault(e[2],{})[e[3]]=e[4]
            elif e[0]=='WIRE' and len(e)>=3:
                wires[e[1]]={'segs':e[2]}
            elif e[0]=='TEXT' and len(e)>=6:
                texts[e[1]]={'x':e[2],'y':e[3],'rot':e[4],'text':e[5]}

        # Assign NET names to wires from ATTR
        wire_net = {}
        for wid in wires:
            attrs = wire_attrs.get(wid, {})
            net = attrs.get('NET', '')
            if net: wire_net[wid] = net

        bom, pwr_syms = [], []
        for cid,info in comps.items():
            des=info['a'].get('Designator','')
            nm=(info['name'] or '').replace('.1','').replace('.2','')
            if des:
                bom.append({'cid':cid,'des':des,'name':nm,'val':info['a'].get('Name',''),
                           'fp':info['a'].get('Origin Footprint',''),'sp':info['a'].get('Supplier Part',''),
                           'x':info['x'],'y':info['y'],'rot':info['rot'],'mir':info['mir']})
            elif nm and nm not in ('','Drawing-Symbol_A4'):
                # Resolve actual net name from connected wire's NET attr
                # instead of using component library title
                sx, sy = snap(info['x']), snap(info['y'])
                resolved_net = None
                for wid, w in wires.items():
                    wn = wire_net.get(wid, '')
                    if not wn: continue
                    for s in w['segs']:
                        if len(s) >= 4:
                            if (snap(s[0]),snap(s[1])) == (sx,sy) or (snap(s[2]),snap(s[3])) == (sx,sy):
                                resolved_net = wn; break
                            if pt_on_seg(sx, sy, s[0], s[1], s[2], s[3], 1.0):
                                resolved_net = wn; break
                    if resolved_net: break
                pwr_syms.append({'cid':cid,'net':resolved_net or nm,'x':info['x'],'y':info['y']})

        # === NET TRACING ===
        uf = UF()

        # Collect wire points and segments
        all_segs, wire_pts = [], defaultdict(set)
        for wid,w in wires.items():
            for s in w['segs']:
                if len(s)>=4:
                    p1,p2=(snap(s[0]),snap(s[1])),(snap(s[2]),snap(s[3]))
                    all_segs.append((s[0],s[1],s[2],s[3],wid))
                    wire_pts[wid].add(p1); wire_pts[wid].add(p2)

        # 1. Same WIRE = same net (multi-segment)
        for wid,pts in wire_pts.items():
            pl=list(pts)
            for i in range(1,len(pl)): uf.union(pl[0],pl[i])

        # 2. Same coordinate = connected
        cp=defaultdict(list)
        for wid,pts in wire_pts.items():
            for pt in pts: cp[pt].append(wid)
        for pt,wids in cp.items():
            if len(wids)>1:
                reps=[list(wire_pts[w])[0] for w in wids]
                for i in range(1,len(reps)): uf.union(reps[0],reps[i])

        # 3. T-junctions
        aep=set()
        for pts in wire_pts.values(): aep.update(pts)
        for ep in aep:
            for(x1,y1,x2,y2,wid) in all_segs:
                s1,s2=(snap(x1),snap(y1)),(snap(x2),snap(y2))
                if ep==s1 or ep==s2: continue
                if pt_on_seg(ep[0],ep[1],x1,y1,x2,y2,1.0): uf.union(ep,s1)

        # 4. *** KEY: Same NET name = connected ***
        net_name_wires = defaultdict(list)  # net_name -> [wire_ids]
        for wid, net in wire_net.items():
            if net: net_name_wires[net].append(wid)

        for net, wids in net_name_wires.items():
            if len(wids) > 1:
                reps = [list(wire_pts[w])[0] for w in wids if wire_pts[w]]
                for i in range(1, len(reps)):
                    uf.union(reps[0], reps[i])

        # Component pin positions
        # Some EasyEDA Pro symbols have pin x-coordinates inverted relative to
        # the component placement. To handle this, we register both the normal
        # and x-negated pin offsets, then keep whichever actually touches a wire.
        cpins={}
        cpins_alt={}  # x-negated alternatives
        for it in bom:
            sp=self.sym_pins(it['name'].lower())
            if not sp: continue
            for pn,pi in sp.items():
                rx,ry=rotpt(pi['x'],pi['y'],it['rot'],it['mir'])
                ax,ay=snap(it['x']+rx),snap(it['y']+ry)
                cpins[(it['des'],pn)]=(ax,ay)
                uf.find((ax,ay))
                # x-negated alternative
                rx2,ry2=rotpt(-pi['x'],pi['y'],it['rot'],it['mir'])
                ax2,ay2=snap(it['x']+rx2),snap(it['y']+ry2)
                if (ax2,ay2) != (ax,ay):
                    cpins_alt[(it['des'],pn)]=(ax2,ay2)
                    uf.find((ax2,ay2))

        for ps in pwr_syms: uf.find((snap(ps['x']),snap(ps['y'])))

        # Connect pins & power to wires
        def link(px,py):
            pt=(px,py)
            if pt in cp:
                for wid in cp[pt]:
                    pl=list(wire_pts[wid])
                    if pl: uf.union(pt,pl[0])
                return True
            for(x1,y1,x2,y2,wid) in all_segs:
                if pt_on_seg(px,py,x1,y1,x2,y2,1.0):
                    uf.union(pt,(snap(x1),snap(y1))); return True
            return False

        # Link primary pin positions; if primary fails, try x-negated alt
        for key,(px,py) in cpins.items():
            if not link(px,py):
                alt = cpins_alt.get(key)
                if alt:
                    if link(alt[0],alt[1]):
                        # Alt matched — update cpins to use corrected coordinate
                        cpins[key] = alt
        for ps in pwr_syms: link(snap(ps['x']),snap(ps['y']))

        # Build connections
        rc=defaultdict(set)
        for(des,pn),(px,py) in cpins.items(): rc[uf.find((px,py))].add(des)
        rp=defaultdict(set)
        for ps in pwr_syms: rp[uf.find((snap(ps['x']),snap(ps['y'])))].add(f"PWR:{ps['net']}")

        nc=defaultdict(set)
        for root,cs in rc.items():
            an=cs|rp.get(root,set())
            for n in an: nc[n]|=(an-{n})

        def sk(it):
            d=it['des'];p=''.join(c for c in d if c.isalpha());n=''.join(c for c in d if c.isdigit())
            return(p,int(n) if n else 0)
        bom.sort(key=sk)

        # Per-named-net component membership (for summary mode)
        sig_net_members = defaultdict(set)  # net_name -> {designators}
        pwr_nets_skip = {'GND', '3V3', '+12V', '+5V', 'VCC', 'P_GND', 'VBUS'}
        # Map: UF root -> net_name (from wire_net assignments)
        root_to_net = {}
        for wid, net in wire_net.items():
            if net and net not in pwr_nets_skip and wire_pts.get(wid):
                rep = list(wire_pts[wid])[0]
                root = uf.find(rep)
                root_to_net[root] = net
        # Also from power symbols with signal net names
        for ps in pwr_syms:
            if ps['net'] not in pwr_nets_skip:
                root = uf.find((snap(ps['x']), snap(ps['y'])))
                root_to_net[root] = ps['net']
        # Map each component to its signal nets
        for (des, pn), (px, py) in cpins.items():
            root = uf.find((px, py))
            net = root_to_net.get(root)
            if net:
                sig_net_members[net].add(des)

        return {'bom':bom,'wires':len(wires),'texts':list(texts.values()),
                'power_symbols':pwr_syms,
                'net_connections':{k:sorted(list(v)) for k,v in nc.items()},
                'wire_nets':dict(wire_net),
                'signal_net_members':{k:sorted(list(v)) for k,v in sig_net_members.items()}}

    def ic_pins(self, placed_names=None):
        """Return pin maps only for ICs actually placed on schematic sheets.
        If placed_names is given, only symbols matching those names are included.
        This prevents library-only symbols (e.g. TPS61088 leftover from copy)
        from appearing in analysis results."""
        self.cur.execute("SELECT title,dataStr FROM components WHERE docType=2 AND dataStr IS NOT NULL")
        out={}
        for r in self.cur.fetchall():
            title = r[0]
            if placed_names is not None:
                # Match against placed component names (strip .N suffix)
                t_lower = title.lower()
                if t_lower not in placed_names and re.sub(r'\.\d+$','',t_lower) not in placed_names:
                    continue
            p=self.sym_pins(title)
            if len(p)>2: out[title]={n:{"name":i["name"],"number":i["number"]} for n,i in p.items()}
        return out

    def analyze(self, sf=None):
        # First pass: collect all component names placed on sheets
        placed_names = set()
        sheets_data = []
        for s in self.sheets():
            if sf and s['title']!=sf: continue
            sd=self.parse_sheet(s['title'])
            sd.update({k:s[k] for k in('title','display_title','sheet_id')})
            sheets_data.append(sd)
            for b in sd.get('bom',[]):
                nm = (b.get('name','') or '').lower()
                placed_names.add(nm)
                placed_names.add(re.sub(r'\.\d+$','',nm))
        res={'project':self.project(),'sheets':sheets_data,
             'ic_pin_maps':self.ic_pins(placed_names)}
        return res

def pr(result):
    p=result['project']
    print(f"{'='*70}\nProject: {p['name']}\n{'='*70}")
    if result.get('ic_pin_maps'):
        print(f"\n{'='*70}\nIC PIN MAPS\n{'='*70}")
        for ic,pins in sorted(result['ic_pin_maps'].items()):
            print(f"\n  {ic} ({len(pins)} pins):")
            for n in sorted(pins,key=lambda x:int(x) if x.isdigit() else 999):
                print(f"    Pin {n:3s}: {pins[n]['name']}")
    for sh in result['sheets']:
        print(f"\n{'='*70}\nSheet: {sh.get('display_title',sh.get('title','?'))}\n  Wires: {sh.get('wires',0)}\n{'='*70}")
        bom=sh.get('bom',[])
        if bom:
            grp=defaultdict(list)
            for it in bom: grp[''.join(c for c in it['des'] if c.isalpha())].append(it)
            print(f"\n--- BOM ({len(bom)} parts) ---")
            for pfx in['U','Q','R','C','L','D','CN','LD','LED','SW']:
                items=grp.get(pfx,[])
                if not items: continue
                print(f"\n  {pfx} ({len(items)} parts):")
                for it in items:
                    fp=f" [{it['fp']}]" if it['fp'] else ""
                    print(f"    {it['des']:8s} {it['name']:30s}{fp}")
                    print(f"             pos=({it['x']:.0f}, {it['y']:.0f}) rot={it['rot']}")
        nets=sh.get('net_connections',{})
        if nets:
            print(f"\n--- Net Connections ---")
            for c,conn in sorted(nets.items()):
                if not c.startswith('PWR:'): print(f"  {c:8s} <> {', '.join(conn)}")
        pwr=sh.get('power_symbols',[])
        if pwr:
            print(f"\n--- Power Symbols ({len(pwr)}) ---")
            for ps in pwr: print(f"  {ps['net']:6s} at ({ps['x']:.0f}, {ps['y']:.0f})")
        texts=sh.get('texts',[])
        if texts:
            print(f"\n--- Text Annotations ---")
            for t in texts: print(f"  \"{t['text']}\" at ({t['x']:.0f}, {t['y']:.0f})")


# ──────────────────────────────────────────────────────────
# --summary mode
# ──────────────────────────────────────────────────────────

def decode_r(name):
    """Decode resistor MPN → human-readable value string."""
    name = re.sub(r'\.\d+$', '', name)
    # Direct value
    if re.match(r'^[\d.]+\s*[kKmMΩ]*[Ωo]?$', name):
        return name
    # 0603WAF / 0805W8F / 1206W4F: value encoding after prefix
    m = re.search(r'(?:0603WAF|0805W8F|1206W4F)(\d+)(K|M|R|L)(\w*)', name)
    if m:
        digits = m.group(1)
        unit_char = m.group(2)
        trail = m.group(3)
        # L suffix = milli-ohm: e.g. 1206W4F200LT5E → 200mΩ
        if unit_char == 'L':
            return f"{int(digits)}mΩ"
        # K/M/R = value unit: digits before K/M/R are the value
        # e.g. 0805W8F200KT5E → 200K → 200kΩ
        #      0603WAF1002T5E → no K/M/R → fall through to 4-digit code
        if unit_char == 'K':
            return f"{digits}kΩ"
        if unit_char == 'M':
            return f"{digits}MΩ"
        if unit_char == 'R':
            # R is decimal point: e.g. 4R7 = 4.7Ω
            return f"{digits}Ω"
    # 0603WAF / 0805W8F / 1206W4F: 4-digit code (no K/M/R/L unit char)
    m = re.search(r'(?:0603WAF|0805W8F|1206W4F)(\d{3,4})[T]', name)
    if m:
        code = m.group(1)
        if len(code) == 4:
            sig = int(code[:3])
            exp = int(code[3])
            val = sig * (10 ** exp)
        elif len(code) == 3:
            sig = int(code[:2])
            exp = int(code[2])
            val = sig * (10 ** exp)
        else:
            return name
        if val >= 1_000_000: return f"{val/1e6:.1f}MΩ"
        if val >= 1000:
            k = val / 1e3
            return f"{k:.0f}kΩ" if k == int(k) else f"{k:.1f}kΩ"
        return f"{val}Ω"
    # RC0603FR-07xxxKL: "07" is tolerance code, value follows
    # RC0603FR-07100KL → 100K → 100kΩ
    # RC0603FR-0710KL  → 10K  → 10kΩ
    # RC0603FR-07100RL → 100R → 100Ω
    m = re.search(r'RC\d+FR-07(\d+\.?\d*)(K|M|R)L', name)
    if m:
        val = m.group(1)
        unit = m.group(2)
        if unit == 'K': return f"{val}kΩ"
        if unit == 'M': return f"{val}MΩ"
        if unit == 'R': return f"{val}Ω"
        return f"{val}Ω"
    # RT0603BRD07xxxKL: same structure as RC
    # RT0603BRD0750KL → 50K → 50kΩ
    m = re.search(r'RT\d+BRD07(\d+\.?\d*)(K|M|R)L', name)
    if m:
        val = m.group(1)
        unit = m.group(2)
        if unit == 'K': return f"{val}kΩ"
        if unit == 'M': return f"{val}MΩ"
        if unit == 'R': return f"{val}Ω"
    # MFJ06HR010FT → 10mΩ
    m = re.search(r'MFJ\d+HR(\d+)FT', name)
    if m:
        code = m.group(1)
        val = int(code) if int(code) > 0 else 1
        return f"{val}mΩ"
    # FRM series sense resistor
    m = re.search(r'FRM\d+WFR(\d+)TM', name)
    if m:
        code = m.group(1)
        return f"{int(code)}mΩ"
    return name

def pr_summary(result):
    """Print compact summary: cross-sheet nets, floating parts, FB dividers, IC pin map."""
    p = result['project']
    sheets = result['sheets']
    print(f"Project: {p['name']}  |  Sheets: {len(sheets)}")
    print(f"{'─'*60}")

    # ── 1. Per-sheet overview ──
    for sh in sheets:
        title = sh.get('display_title', sh.get('title', '?'))
        bom = sh.get('bom', [])
        pwr = sh.get('power_symbols', [])
        wnets = sh.get('wire_nets', {})
        ic_n = sum(1 for b in bom if b['des'].startswith('U'))
        r_n = sum(1 for b in bom if b['des'].startswith('R'))
        c_n = sum(1 for b in bom if b['des'].startswith('C'))
        print(f"  {title}: {len(bom)} parts (IC:{ic_n} R:{r_n} C:{c_n})"
              f" | {len(set(wnets.values()))} nets | {len(pwr)} pwr syms")

    # ── 2. Cross-sheet net comparison ──
    if len(sheets) >= 2:
        all_nets = {}
        for sh in sheets:
            title = sh.get('title', '')
            nets_on = set(sh.get('wire_nets', {}).values())
            for ps in sh.get('power_symbols', []):
                nets_on.add(ps['net'])
            all_nets[title] = nets_on
        titles = list(all_nets.keys())
        shared = set.intersection(*all_nets.values()) if all_nets else set()
        print(f"\n{'─'*60}")
        print(f"Cross-sheet nets:")
        print(f"  Shared ({len(shared)}): {', '.join(sorted(shared))}")
        for t in titles:
            excl = all_nets[t] - shared
            if excl:
                print(f"  {t}-only ({len(excl)}): {', '.join(sorted(excl))}")

    # ── 3. Floating components ──
    print(f"\n{'─'*60}")
    print(f"Floating components (no net connections):")
    found = False
    for sh in sheets:
        title = sh.get('title', '')
        nc = sh.get('net_connections', {})
        for b in sh.get('bom', []):
            if b['des'] not in nc or not nc[b['des']]:
                print(f"  ⚠ {b['des']:8s} ({b['name']}) on {title}")
                found = True
    if not found:
        print(f"  None")

    # ── 4. FB voltage dividers (smart: per-IC, only direct FB-pin connections) ──
    # Build per-sheet non-power net graph (exclude GND, 3V3, +12V, etc.)
    pwr_net_names = {'GND', '3V3', '+12V', '+5V', 'VCC', 'VBUS', 'P_GND'}
    print(f"\n{'─'*60}")
    print(f"FB voltage dividers:")
    
    # Known IC FB pin info: IC_name_substring → (Vref, FB_pin_name)
    fb_info = {
        'TPS61088': (0.6, 'FB'),
        'BQ24650':  (2.09, 'VFB'),
        'LGS5145':  (0.8, 'FB'),
    }
    
    ic_pin_maps = result.get('ic_pin_maps', {})
    
    for sh in sheets:
        nc = sh.get('net_connections', {})
        bom_map = {b['des']: b for b in sh.get('bom', [])}
        
        for des, info in bom_map.items():
            if not des.startswith('U'):
                continue
            ic_name = info['name']
            
            # Find matching FB info
            matched_fb = None
            for key, (vref, fb_pin) in fb_info.items():
                if key in ic_name:
                    matched_fb = (vref, fb_pin, key)
                    break
            if not matched_fb:
                continue
            
            vref, fb_pin_name, ic_key = matched_fb
            conns = nc.get(des, set())
            
            # Get only resistors directly connected (non-power path)
            # Filter: only R-prefixed, skip power-only connections
            direct_rs = sorted([c for c in conns if c.startswith('R')])
            
            # Among direct_rs, find pairs where both connect to this IC
            # AND one connects to GND → that's the bottom resistor
            # Strategy: find R_top (connects to IC FB and R_bot) and R_bot (connects to R_top and GND)
            divider_pairs = []
            for ra in direct_rs:
                ra_conns = nc.get(ra, set())
                for rb in direct_rs:
                    if ra >= rb:
                        continue
                    rb_conns = nc.get(rb, set())
                    # Check: ra and rb connected to each other?
                    if rb not in ra_conns:
                        continue
                    # Check: one goes to GND
                    a_gnd = 'PWR:GND' in ra_conns
                    b_gnd = 'PWR:GND' in rb_conns
                    if not (a_gnd or b_gnd):
                        continue
                    # Skip if both go to GND (bypass, not divider)
                    if a_gnd and b_gnd:
                        continue
                    
                    if a_gnd:
                        r_bot, r_top = ra, rb
                    else:
                        r_bot, r_top = rb, ra
                    
                    # Check r_top doesn't also connect to GND
                    if 'PWR:GND' in nc.get(r_top, set()):
                        continue
                    
                    divider_pairs.append((r_top, r_bot))
            
            for r_top, r_bot in divider_pairs:
                v_top = decode_r(bom_map.get(r_top, {}).get('name', ''))
                v_bot = decode_r(bom_map.get(r_bot, {}).get('name', ''))
                
                rt_ohm = _parse_ohm(v_top)
                rb_ohm = _parse_ohm(v_bot)
                
                if rt_ohm and rb_ohm and rb_ohm > 0:
                    vout = vref * (1 + rt_ohm / rb_ohm)
                    print(f"  {des} ({ic_key}): {r_top}={v_top} / {r_bot}={v_bot}"
                          f"  → Vout={vref}×(1+{rt_ohm/1e3:.0f}k/{rb_ohm/1e3:.1f}k)={vout:.2f}V")
                else:
                    print(f"  {des} ({ic_key}): {r_top}={v_top} / {r_bot}={v_bot}")

    # ── 5. IC signal-net connections (power-net transitive excluded) ──
    print(f"\n{'─'*60}")
    print(f"IC signal-net connections:")
    for sh in sheets:
        title = sh.get('title', '')
        snm = sh.get('signal_net_members', {})
        nc = sh.get('net_connections', {})
        bom_map = {b['des']: b for b in sh.get('bom', [])}
        
        for des in sorted(bom_map.keys()):
            if not des.startswith('U'):
                continue
            info = bom_map[des]
            
            # Find signal nets this IC is on
            ic_nets = {}
            for net, members in snm.items():
                if des in members:
                    others = sorted([m for m in members if m != des])
                    if others:
                        ic_nets[net] = others
            
            # Power nets from nc
            conns = nc.get(des, set())
            pwr = sorted([c.replace('PWR:','') for c in conns if c.startswith('PWR:')])
            
            print(f"  {des:6s} ({info['name']:20s}) [{title}]  pwr:{','.join(pwr) if pwr else '-'}")
            for net, members in sorted(ic_nets.items()):
                print(f"         {net}: {', '.join(members)}")
    
    print(f"{'─'*60}")

def _parse_ohm(s):
    """Parse resistance string to ohms."""
    s = s.strip()
    m = re.match(r'([\d.]+)\s*(MΩ|kΩ|Ω|mΩ)?', s)
    if not m:
        return None
    v = float(m.group(1))
    u = m.group(2) or 'Ω'
    if u == 'MΩ': return v * 1e6
    if u == 'kΩ': return v * 1e3
    if u == 'mΩ': return v * 1e-3
    return v


def main():
    if len(sys.argv)<2: print("Usage: python3 analyze_eprj.py <file.eprj> [--json] [--sheet <name>]"); sys.exit(1)
    fp=sys.argv[1]; js='--json' in sys.argv; sm='--summary' in sys.argv
    sf=sys.argv[sys.argv.index('--sheet')+1] if '--sheet' in sys.argv and sys.argv.index('--sheet')+1<len(sys.argv) else None
    a=Analyzer(fp)
    try:
        r=a.analyze(sf)
        if sm: pr_summary(r)
        elif js: print(json.dumps(r,indent=2,ensure_ascii=False,default=str))
        else: pr(r)
    finally: a.close()

if __name__=='__main__': main()
