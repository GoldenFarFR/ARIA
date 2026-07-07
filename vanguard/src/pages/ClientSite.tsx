import { useEffect } from 'react'

/**
 * ClientSite: public product landing (English-first) for ARIA's analysis reports.
 *
 * Separate surface from the ZHC vitrine (VanguardSite). Open to everyone; the
 * marketing campaign funnels curious visitors here. Design validated with the
 * client (grey graphite ground, report "screens" in 3D, standard/premium
 * comparison). All styles are scoped under `.aria-client` so nothing leaks into
 * the rest of the app. Content is currently static (illustrative sample); it
 * will later be driven by the backend report engine.
 */
const CSS = `
.aria-client{--bg:#2e2f34;--bg-2:#34353b;--bg-3:#26272b;--line:rgba(230,196,99,0.10);
  --gold:#c9a227;--gold-light:#e6c463;--gold-deep:#c2a24a;--emerald:#2aa189;--emerald-deep:#1f8a74;
  --ivory:#f6f2e9;--ink-warm:#2a2620;--mute-warm:#7a7264;--paper-line:#e7deca;--rose:#d98a8a;
  --text:#e0e1e5;--muted:#a8abb3;--faint:#7f828c;
  --serif:"Cormorant Garamond",Georgia,"Times New Roman",serif;
  --sans:"Inter",-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;overflow-x:hidden;}
.aria-client *{box-sizing:border-box;}
.aria-client .wrap{max-width:1120px;margin:0 auto;padding:0 30px;}
.aria-client .nav{position:fixed;top:0;left:0;right:0;z-index:40;height:64px;background:rgba(38,39,43,0.72);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);}
.aria-client .nav .wrap{display:flex;align-items:center;justify-content:space-between;height:100%;}
.aria-client .brand{display:flex;align-items:center;gap:11px;font-family:var(--serif);color:#f1efe9;font-size:1.05rem;}
.aria-client .mono{width:26px;height:26px;border-radius:50%;border:1px solid var(--gold);display:grid;place-items:center;color:var(--gold-light);font-family:var(--serif);font-size:0.82rem;}
.aria-client .brand .zhc{color:var(--gold-deep);}
.aria-client .badge{font-size:0.6rem;letter-spacing:0.28em;text-transform:uppercase;color:var(--gold-deep);}
.aria-client .split{display:grid;grid-template-columns:1fr 1fr;align-items:center;gap:56px;}
.aria-client .kicker{font-size:0.63rem;letter-spacing:0.3em;text-transform:uppercase;color:var(--gold-deep);margin:0 0 20px;}
.aria-client h1,.aria-client h2{font-family:var(--serif);font-weight:500;color:#f4f1ea;letter-spacing:-0.015em;margin:0;text-wrap:balance;}
.aria-client .lead-h{font-size:clamp(2.4rem,5.4vw,3.7rem);line-height:1.02;}
.aria-client .g{background:linear-gradient(120deg,#e6c463 0%,#c9a227 45%,#2aa189 100%);-webkit-background-clip:text;background-clip:text;color:transparent;}
.aria-client .sub{color:var(--muted);font-size:1.08rem;line-height:1.65;max-width:34ch;margin:22px 0 0;}
.aria-client .sub b{color:#f4f1ea;font-weight:600;}
.aria-client .hero{min-height:100vh;display:flex;align-items:center;padding:96px 0 60px;background:radial-gradient(ellipse 100% 80% at 70% 20%,#34353b 0%,#2e2f34 55%,#26272b 100%);}
.aria-client .hero h1{font-size:clamp(3rem,8vw,5.6rem);line-height:0.96;}
.aria-client .eyebrow{font-size:0.66rem;letter-spacing:0.32em;text-transform:uppercase;color:var(--gold);display:flex;align-items:center;gap:12px;margin:0 0 28px;}
.aria-client .eyebrow::before{content:"";width:30px;height:1px;background:linear-gradient(90deg,var(--gold),var(--emerald));}
.aria-client .cta-row{display:flex;flex-wrap:wrap;align-items:center;gap:16px 24px;margin-top:36px;}
.aria-client .btn{display:inline-flex;align-items:center;gap:10px;padding:16px 32px;font-size:0.92rem;font-weight:700;letter-spacing:0.03em;color:#151515;text-decoration:none;border-radius:3px;background:linear-gradient(120deg,#8a6a13,#e6c463 32%,#c9a227 58%,#1f8a74 100%);background-size:170% 100%;box-shadow:0 1px 0 rgba(255,255,255,0.25) inset,0 12px 34px rgba(201,162,39,0.22);transition:background-position .5s,transform .25s,box-shadow .25s;cursor:pointer;border:0;}
.aria-client .btn:hover{background-position:100% 0;transform:translateY(-2px);box-shadow:0 1px 0 rgba(255,255,255,0.3) inset,0 16px 42px rgba(201,162,39,0.32);}
.aria-client .ghost{color:var(--gold-light);text-decoration:none;font-size:0.9rem;border-bottom:1px solid rgba(230,196,99,0.3);padding-bottom:2px;}
.aria-client .ghost:hover{color:#f4f1ea;border-color:var(--emerald);}
.aria-client .beat{padding:110px 0;border-top:1px solid var(--line);}
.aria-client .scene{perspective:1500px;}
.aria-client .card3d{transform:rotateX(7deg) rotateY(-11deg);transform-style:preserve-3d;transition:transform 1s cubic-bezier(.2,.7,.2,1),opacity .9s ease;opacity:0;filter:drop-shadow(0 40px 70px rgba(0,0,0,0.5));}
.aria-client .card3d.right{transform:rotateX(7deg) rotateY(11deg);}
.aria-client .card3d.in{opacity:1;transform:rotateX(4deg) rotateY(-6deg);}
.aria-client .card3d.right.in{transform:rotateX(4deg) rotateY(6deg);}
.aria-client .card3d:hover{transform:rotateX(1deg) rotateY(0deg);}
.aria-client .rep{border-radius:14px;overflow:hidden;box-shadow:0 2px 0 rgba(255,255,255,0.04) inset;max-width:520px;}
.aria-client .rep-mast{background-color:#0a0e1a;background-image:linear-gradient(165deg,#101a2e 0%,#0a0e1a 55%,#0c1020 100%);padding:26px 28px 24px;}
.aria-client .rep-mast .word{display:flex;align-items:center;gap:9px;font-size:0.6rem;letter-spacing:0.24em;text-transform:uppercase;color:var(--gold);margin-bottom:20px;}
.aria-client .drop{width:22px;height:22px;border-radius:50% 50% 50% 0;transform:rotate(45deg);background:radial-gradient(circle at 35% 30%,#f2dd98,#c9a227 55%,#8a6a13);box-shadow:0 0 14px rgba(201,162,39,0.4);}
.aria-client .rep-mast h3{font-family:var(--serif);font-weight:500;color:var(--ivory);font-size:1.7rem;margin:0;}
.aria-client .rep-mast h3.sm{font-size:1.4rem;}
.aria-client .rep-mast .meta{font-size:0.72rem;color:#8fa0ad;margin-top:6px;font-variant-numeric:tabular-nums;}
.aria-client .rep-pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px;}
.aria-client .pill{font-size:0.68rem;font-weight:600;letter-spacing:0.04em;padding:5px 11px;border-radius:999px;border:1px solid;}
.aria-client .pill.buy{color:#10131f;border:0;background:linear-gradient(135deg,#b0862b,#e6c463 55%,#c9a227);}
.aria-client .pill.risk{color:var(--gold-light);border-color:rgba(31,138,116,0.6);background:rgba(15,107,92,0.16);}
.aria-client .pill.pot{color:var(--gold-light);border-color:rgba(201,162,39,0.55);background:rgba(201,162,39,0.08);}
.aria-client .pill.avoid{color:#93a09b;border-color:rgba(147,160,155,0.5);background:rgba(147,160,155,0.1);}
.aria-client .pill.extreme{color:var(--rose);border-color:rgba(217,138,138,0.6);background:rgba(163,74,42,0.18);}
.aria-client .rep-mast.std{position:relative;background-color:#0a0a0d;background-image:linear-gradient(165deg,#151318 0%,#0a0a0d 55%,#0f0b0f 100%);}
.aria-client .rep-mast.std::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#e59ac0 0%,#4bbf9a 50%,#e6c463 100%);}
.aria-client .pill.sp-r{color:#f0b6d2;border-color:rgba(229,154,192,0.5);background:rgba(229,154,192,0.10);}
.aria-client .pill.sp-g{color:#8fe0c4;border-color:rgba(75,191,154,0.5);background:rgba(75,191,154,0.10);}
.aria-client .rep-strip{background:var(--ivory);color:var(--mute-warm);font-size:0.66rem;letter-spacing:0.14em;text-transform:uppercase;padding:12px 28px;text-align:center;}
.aria-client .rep-ivory{background:var(--ivory);padding:26px 28px 22px;}
.aria-client .sec-head{display:flex;align-items:center;gap:14px;margin-bottom:6px;}
.aria-client .sec-head .t{font-family:var(--serif);font-size:1.15rem;color:#0b1f3a;white-space:nowrap;}
.aria-client .sec-head .rule{height:1px;flex:1;background:linear-gradient(to right,#dcd2b4,rgba(15,107,92,0.35),rgba(220,210,180,0));}
.aria-client .sig{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--paper-line);}
.aria-client .sig:last-child{border-bottom:0;}
.aria-client .sig .l{font-size:0.66rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--mute-warm);font-weight:600;}
.aria-client .sig .v{font-size:0.9rem;color:var(--ink-warm);font-weight:600;font-variant-numeric:tabular-nums;}
.aria-client .sig .v.ok{color:var(--emerald-deep);}
.aria-client .rr-frame{border-radius:12px;padding:2px;background:linear-gradient(120deg,#8a6a13 0%,#e6c463 30%,#c9a227 55%,#1f8a74 85%,#0f6b5c 100%);max-width:520px;}
.aria-client .rr-inner{background:#0b1220;border-radius:10px;padding:5px;}
.aria-client .rr-brd{border:1px solid rgba(230,196,99,0.3);border-radius:7px;padding:22px 18px 16px;}
.aria-client .rr-cols{display:grid;grid-template-columns:1fr 1fr 1fr;}
.aria-client .rr-col{text-align:center;padding:0 8px;}
.aria-client .rr-col.mid{border-left:1px solid rgba(230,196,99,0.18);border-right:1px solid rgba(230,196,99,0.18);}
.aria-client .rr-col .lab{font-size:0.62rem;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:var(--gold);line-height:1.4;}
.aria-client .rr-col .num{margin-top:12px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums;}
.aria-client .rr-col .num.up{font-size:1.5rem;color:#35b295;}
.aria-client .rr-col .num.rr{font-size:2.6rem;color:var(--gold-light);}
.aria-client .rr-col .num.dn{font-size:1.5rem;color:var(--rose);}
.aria-client .rr-cap{text-align:center;margin-top:16px;font-size:0.74rem;color:#93a09b;line-height:1.6;}
.aria-client .editions{display:grid;grid-template-columns:1fr 1fr;gap:26px;margin-top:46px;align-items:start;}
.aria-client .ed{display:flex;flex-direction:column;gap:14px;}
.aria-client .ed .rep{max-width:none;width:100%;}
.aria-client .ed .tag{display:flex;align-items:baseline;justify-content:space-between;padding:0 4px;}
.aria-client .ed .tag .name{font-family:var(--serif);color:#f4f1ea;font-size:1.1rem;}
.aria-client .ed .tag .price{font-family:var(--serif);color:var(--gold-light);font-size:1.5rem;}
.aria-client .ed .tag .price small{font-family:var(--sans);font-size:0.7rem;color:var(--muted);}
.aria-client .center{text-align:center;}
.aria-client .offer{padding:110px 0;border-top:1px solid var(--line);text-align:center;}
.aria-client .price{font-family:var(--serif);font-size:clamp(3.4rem,9vw,5rem);color:var(--gold-light);line-height:1;}
.aria-client .price small{font-family:var(--sans);font-size:1.1rem;color:var(--muted);}
.aria-client .terms{display:flex;justify-content:center;flex-wrap:wrap;gap:14px 30px;margin-top:24px;font-size:0.7rem;letter-spacing:0.16em;text-transform:uppercase;color:var(--gold-deep);}
.aria-client .terms span{display:flex;align-items:center;gap:8px;}
.aria-client .terms i{width:5px;height:5px;border-radius:50%;background:var(--emerald);font-style:normal;}
.aria-client .final{padding:130px 0 140px;text-align:center;background:radial-gradient(ellipse 70% 60% at 50% 40%,rgba(42,161,137,0.08),transparent 65%);}
.aria-client .final h2{font-size:clamp(2.6rem,7vw,4.4rem);line-height:1;}
.aria-client .final p{color:var(--muted);margin:20px auto 0;max-width:40ch;}
.aria-client .final .fine{color:var(--faint);font-size:0.8rem;margin-top:16px;}
.aria-client footer{border-top:1px solid var(--line);padding:32px 0;}
.aria-client footer .wrap{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px;font-size:0.78rem;color:var(--faint);}
.aria-client footer .dom{color:var(--gold-deep);text-decoration:none;}
.aria-client .legal-links{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:0.74rem;}
.aria-client .legal-links a{color:var(--faint);text-decoration:none;}
.aria-client .legal-links a:hover{color:var(--gold-light);}
.aria-client .disc{max-width:1120px;margin:0 auto;padding:0 30px 44px;font-size:0.72rem;color:#6a6d76;line-height:1.6;font-style:italic;}
.aria-client .rv{opacity:0;transform:translateY(18px);transition:opacity .8s ease,transform .8s ease;}
.aria-client .rv.in{opacity:1;transform:none;}
@media (prefers-reduced-motion:reduce){.aria-client .rv,.aria-client .card3d{opacity:1 !important;transform:none !important;transition:none;}}
@media (max-width:860px){
  .aria-client .split{grid-template-columns:1fr;gap:40px;}
  .aria-client .scene{perspective:none;}
  .aria-client .card3d,.aria-client .card3d.right{transform:none;}
  .aria-client .hero{min-height:auto;}
  .aria-client .sub{max-width:100%;}
  .aria-client .rep,.aria-client .rr-frame{margin:0 auto;}
  .aria-client .editions{grid-template-columns:1fr;}
}
`

export function ClientSite() {
  useEffect(() => {
    const els = document.querySelectorAll('.aria-client .rv, .aria-client .card3d')
    if (!('IntersectionObserver' in window)) {
      els.forEach((e) => e.classList.add('in'))
      return
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((en) => {
          if (en.isIntersecting) {
            en.target.classList.add('in')
            io.unobserve(en.target)
          }
        })
      },
      { threshold: 0.16 },
    )
    els.forEach((e) => io.observe(e))
    return () => io.disconnect()
  }, [])

  return (
    <div className="aria-client">
      <style>{CSS}</style>

      <nav className="nav">
        <div className="wrap">
          <div className="brand">
            <span className="mono">A</span> Aria Vanguard <span className="zhc">ZHC</span>
          </div>
          <span className="badge">Reports from $30</span>
        </div>
      </nav>

      {/* HERO */}
      <header className="hero">
        <div className="wrap">
          <div className="split">
            <div>
              <p className="eyebrow">Open to all · reports from $30</p>
              <h1>
                The report that<br />
                <span className="g">proves the decision.</span>
              </h1>
              <p className="sub">
                Not a signal. An <b>analysis dossier</b>: quantified, audited, <b>within everyone's reach</b>.
              </p>
              <div className="cta-row">
                <a className="btn" href="#start">Order a report →</a>
                <a className="ghost" href="#signals">See a report ↓</a>
              </div>
            </div>
            <div className="scene">
              <div className="rep card3d">
                <div className="rep-mast">
                  <div className="word"><span className="drop" /> Aria Vanguard ZHC · Analysis report</div>
                  <h3>VC Analysis · sample</h3>
                  <div className="meta">Base · 07/07/2026 · ref. A‑042</div>
                  <div className="rep-pills">
                    <span className="pill buy">BUY</span>
                    <span className="pill risk">Risk · MODERATE</span>
                    <span className="pill pot">Potential 7/10</span>
                  </div>
                </div>
                <div className="rep-strip">Premium edition · private delivery</div>
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* SIGNALS */}
      <section className="beat" id="signals">
        <div className="wrap">
          <div className="split">
            <div className="scene">
              <div className="rep card3d right">
                <div className="rep-ivory">
                  <div className="sec-head"><span className="t">On‑chain signals</span><span className="rule" /></div>
                  <div className="sig"><span className="l">Security score</span><span className="v">72 / 100</span></div>
                  <div className="sig"><span className="l">Liquidity</span><span className="v">$184,000</span></div>
                  <div className="sig"><span className="l">Top holder</span><span className="v">6.4 %</span></div>
                  <div className="sig"><span className="l">Contract</span><span className="v ok">verified ✓</span></div>
                  <div className="sig"><span className="l">Smart money</span><span className="v">4 tracked wallets</span></div>
                  <div className="sig"><span className="l">24h volume</span><span className="v">$96,300</span></div>
                </div>
              </div>
            </div>
            <div>
              <p className="kicker rv">Step 1</p>
              <h2 className="lead-h rv">We compute<br /><span className="g">every signal.</span></h2>
              <p className="sub rv">
                Liquidity, holders, contract, smart money, security. <b>Raw facts</b>, never rumours, and any
                missing data is flagged “insufficient”.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* R/R */}
      <section className="beat">
        <div className="wrap">
          <div className="split">
            <div>
              <p className="kicker rv">Step 2</p>
              <h2 className="lead-h rv">Then we quantify<br /><span className="g">the return.</span></h2>
              <p className="sub rv">
                Upside, downside, and the <b>reward-to-risk ratio</b>: the asymmetry, at a glance. Zero invented numbers.
              </p>
            </div>
            <div className="scene">
              <div className="rr-frame card3d right">
                <div className="rr-inner"><div className="rr-brd">
                  <div className="rr-cols">
                    <div className="rr-col"><div className="lab">Upside potential</div><div className="num up">+142%</div></div>
                    <div className="rr-col mid"><div className="lab">Reward / risk</div><div className="num rr">3.1</div></div>
                    <div className="rr-col"><div className="lab">Downside risk</div><div className="num dn">−46%</div></div>
                  </div>
                  <div className="rr-cap">Favourable asymmetry: the targeted reward is 3.1× the risk taken.</div>
                </div></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* PROOF */}
      <section className="beat">
        <div className="wrap">
          <div className="split">
            <div className="scene">
              <div className="rep card3d">
                <div className="rep-mast" style={{ paddingBottom: 22 }}>
                  <div className="word"><span className="drop" /> Proof engine · analysis audit</div>
                  <h3 className="sm">The judge challenges the analysis</h3>
                  <div className="rep-pills" style={{ marginTop: 16 }}>
                    <span className="pill avoid">Reco · AVOID</span>
                    <span className="pill extreme">Risk · EXTREME</span>
                    <span className="pill pot">Judge · Fragile 6/10</span>
                  </div>
                  <p style={{ margin: '16px 0 0', color: '#9fb0bd', fontSize: '0.82rem', lineHeight: 1.6, fontStyle: 'italic' }}>
                    “Top holder 76.2%, unverified contract. R/R not computable. AVOID recommendation is consistent.”
                  </p>
                </div>
              </div>
            </div>
            <div>
              <p className="kicker rv">Step 3</p>
              <h2 className="lead-h rv">And above all,<br /><span className="g">it knows how to say no.</span></h2>
              <p className="sub rv">
                A second model <b>attacks</b> every analysis. If it doesn't hold up, you know before risking a dollar.
                <b> No automatic execution. You always validate.</b>
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* EDITIONS */}
      <section className="beat center">
        <div className="wrap">
          <p className="kicker rv" style={{ letterSpacing: '0.3em' }}>Two editions, one standard of rigour</p>
          <h2 className="lead-h rv">Standard or <span className="g">premium.</span></h2>
          <p className="sub rv" style={{ marginLeft: 'auto', marginRight: 'auto' }}>
            The same content, the same rigour, two finishes. One-off, or unlimited by subscription.
          </p>
          <div className="editions">
            <div className="ed rv">
              <div className="rep">
                <div className="rep-mast std">
                  <div className="word"><span className="drop" /> Aria Vanguard ZHC · Standard edition</div>
                  <h3 className="sm">VC Analysis · sample</h3>
                  <div className="meta" style={{ color: '#8f8f96' }}>Base · 07/07/2026 · ref. A‑042</div>
                  <div className="rep-pills">
                    <span className="pill buy">BUY</span>
                    <span className="pill sp-r">Risk · MODERATE</span>
                    <span className="pill sp-g">Potential 7/10</span>
                  </div>
                </div>
              </div>
              <div className="tag"><span className="name">Standard</span><span className="price">30&nbsp;$<small>&nbsp;/ report</small></span></div>
            </div>
            <div className="ed rv">
              <div className="rep">
                <div className="rep-mast">
                  <div className="word"><span className="drop" /> Aria Vanguard ZHC · Premium edition</div>
                  <h3 className="sm">VC Analysis · sample</h3>
                  <div className="meta">Base · 07/07/2026 · ref. A‑042</div>
                  <div className="rep-pills">
                    <span className="pill buy">BUY</span>
                    <span className="pill risk">Risk · MODERATE</span>
                    <span className="pill pot">Potential 7/10</span>
                  </div>
                </div>
              </div>
              <div className="tag"><span className="name">Premium</span><span className="price">50&nbsp;$<small>&nbsp;/ report</small></span></div>
            </div>
          </div>
        </div>
      </section>

      {/* OFFER */}
      <section className="offer">
        <div className="wrap">
          <p className="kicker rv" style={{ letterSpacing: '0.3em' }}>Or the subscription · launch</p>
          <div className="price rv">100&nbsp;$<small>&nbsp;/ month</small></div>
          <p className="sub center rv" style={{ margin: '16px auto 0', maxWidth: '40ch' }}>
            <b>Unlimited</b> reports, standard and premium, <b>plus live access to ARIA</b>, your analyst on demand.
          </p>
          <div className="terms rv">
            <span><i />Unlimited reports</span>
            <span><i />ARIA live · LLM</span>
            <span><i />Cancel anytime</span>
            <span><i />Compliance first</span>
          </div>
        </div>
      </section>

      {/* FINAL */}
      <section className="final" id="start">
        <div className="wrap">
          <h2 className="rv">Start now.</h2>
          <p className="rv">A one-off report, or the subscription. ARIA does the analysis. You keep the decision.</p>
          <div style={{ marginTop: 30 }} className="rv"><a className="btn" href="#">Order a report →</a></div>
          <p className="fine rv">No commitment · secure payment</p>
        </div>
      </section>

      <footer>
        <div className="wrap">
          <div className="brand" style={{ fontSize: '0.9rem' }}>
            <span className="mono" style={{ width: 22, height: 22, fontSize: '0.7rem' }}>A</span> Aria Vanguard ZHC
          </div>
          <nav className="legal-links" aria-label="Legal">
            <a href="/terms">Terms</a>
            <a href="/privacy">Privacy</a>
            <a href="/risk">Risk</a>
            <a href="/refunds">Refunds</a>
          </nav>
          <a className="dom" href="#">ariavanguardzhc.com</a>
        </div>
      </footer>
      <p className="disc">
        Aria Vanguard ZHC provides analysis for informational purposes only. Illustrative example. No analysis
        constitutes personalised investment advice or a solicitation. No automatic execution: every decision is
        validated and signed by the client. Past performance does not guarantee future results. Service subject to
        compliance validation before any billing.
      </p>
    </div>
  )
}
