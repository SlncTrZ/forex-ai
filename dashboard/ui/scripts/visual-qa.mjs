import { chromium } from "playwright-core";
const url=process.env.DASHBOARD_QA_URL || "http://127.0.0.1:8765/";
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_BIN||"/usr/bin/chromium",
  args:["--no-sandbox","--disable-dev-shm-usage","--enable-webgl","--enable-unsafe-swiftshader","--use-gl=angle","--ignore-gpu-blocklist"]});
try{
 const width=Number(process.env.QA_WIDTH||1530),height=Number(process.env.QA_HEIGHT||1010);
 const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});
 const problems=[];
 page.on("pageerror",e=>problems.push(String(e.message).slice(0,200)));
 const response=await page.goto(url,{waitUntil:"domcontentloaded",timeout:10000});
 await page.locator(".core-title").waitFor({timeout:8000});
 await page.waitForTimeout(2500);
 const details=await page.evaluate(()=>({
   title:document.title,
   graphCanvas:document.querySelectorAll(".sigma-graph canvas").length,
   graphStatus:document.querySelector(".graph-footer")?.textContent?.slice(0,160),
   panelCount:document.querySelectorAll(".frame").length,
   scrollWidth:document.documentElement.scrollWidth,
   viewportWidth:window.innerWidth,
 }));
 await page.screenshot({path:process.env.QA_SCREENSHOT||"/tmp/forex-v2-qa.png",fullPage:true,timeout:6000});
 console.log(JSON.stringify({http:response?.status(),...details,pageErrors:problems},null,2));
 if(problems.length)process.exitCode=2;
 if(details.scrollWidth>details.viewportWidth)process.exitCode=3;
}finally{await browser.close();}
