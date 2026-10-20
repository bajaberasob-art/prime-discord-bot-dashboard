"""Blind Vaults Auction game."""
from __future__ import annotations
import asyncio,logging,random,math
from dataclasses import dataclass
from typing import Sequence
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image,ImageDraw
from game_engine import CYAN,GREEN,MUTED,PURPLE,RED,WHITE,_font,draw_arabic_text,draw_progress_bar,draw_status_avatar,get_circular_avatar,create_luxury_canvas,draw_glow_panel,image_to_file,UniversalLobbyView
logger=logging.getLogger("PrimeBlindVaults")
VAULTS=(1,2,3);BID_STEPS=(100,250,500);ROUND_SECONDS=10

@dataclass
class VaultPlayer:
    member:object
    cash:int=5000
    points:int=10
    status:str="active"
    @property
    def user_id(self):return int(getattr(self.member,"id"))
    @property
    def name(self):return str(getattr(self.member,"display_name",getattr(self.member,"name","Player")))

@dataclass
class AuctionRound:
    number:int
    contents:dict[int,str]
    multiplier:int
    probe_user_id:int
    probe_hint:str
    current_bid:int=0
    leader_id:int|None=None
    withdrawn:set[int]|None=None
    resolved:bool=False
    def __post_init__(self):
        if self.withdrawn is None:self.withdrawn=set()
    @classmethod
    def create(cls,n,players,rng=None):
        rng=rng or random;order=list(VAULTS);rng.shuffle(order)
        contents={order[0]:"legendary",order[1]:"normal",order[2]:"trap"}
        v=rng.choice(VAULTS);kind=contents[v]
        hints={"legendary":"الخزنة {} فيها الكنز الأسطوري.".format(v),"normal":"الخزنة {} آمنة لكنها ليست الأسطورية.".format(v),"trap":"الخزنة {} مفخخة!".format(v)}
        p=rng.choice([p for p in players if p.status=="active"])
        return cls(n,contents,rng.randint(3,4),p.user_id,hints[kind])

def validate_bid(player,amount,current_bid):
    if player.status!="active":raise ValueError("أنت خارج المزاد.")
    if amount not in BID_STEPS:raise ValueError("مزايدة غير صالحة.")
    if player.cash<current_bid+amount:raise ValueError("رصيدك لا يكفي.")

def resolve_auction_round(players,state,vault):
    if state.resolved:raise ValueError("round_already_resolved")
    if vault not in VAULTS:raise ValueError("invalid_vault")
    p=next((p for p in players if p.user_id==state.leader_id and p.status=="active"),None)
    state.resolved=True
    if p is None or state.current_bid<=0:return {"winner_id":None,"vault":vault,"content":state.contents[vault],"payout":0,"multiplier":state.multiplier}
    bid=state.current_bid;p.cash-=bid;kind=state.contents[vault];gain=0
    if kind=="legendary":gain=bid*state.multiplier;p.cash+=gain;p.points+=5
    elif kind=="normal":gain=int(bid*1.2);p.cash+=gain;p.points+=1
    else:p.points=max(0,p.points-3);p.status="trapped" if p.points==0 else p.status
    return {"winner_id":p.user_id,"vault":vault,"content":kind,"bid":bid,"payout":gain,"multiplier":state.multiplier}

def render_vault_board(players,*,round_number,current_bid,leader_id,seconds_left,result=None,avatars=None):
    im=create_luxury_canvas(1200,790,"BLIND VAULTS AUCTION • مزاد الخزائن الغامضة","الجولة {}".format(round_number),accent=RED if seconds_left<=3 else PURPLE)
    d=ImageDraw.Draw(im)
    for v,(x,y) in zip(VAULTS,[(65,160),(455,160),(845,160)]):
        glow=RED if result and result.get("vault")==v and result.get("content")=="trap" else "#d4af65" if result and result.get("vault")==v and result.get("content")=="legendary" else PURPLE
        draw_glow_panel(im,(x,y,x+285,y+275),outline="#475569",glow=glow);d=ImageDraw.Draw(im)
        d.rounded_rectangle((x+25,y+30,x+260,y+225),radius=18,fill="#1d2637",outline="#9aa8bf",width=6)
        d.rounded_rectangle((x+45,y+48,x+240,y+207),radius=12,fill="#111827",outline="#4b5563",width=3)
        d.ellipse((x+120,y+100,x+165,y+145),fill="#f7c76a",outline="#fff1b8",width=3)
        d.rounded_rectangle((x+139,y+110,x+150,y+165),radius=4,fill="#f7c76a")
        draw_arabic_text(d,(x+142,y+250),"الخزنة {}".format(v),font=_font(23,True),fill=WHITE,anchor="mm")
        if result and result.get("vault")==v:
            kind=result["content"];label="✨ ذهب متناثر!" if kind=="legendary" else "مكسب عادي" if kind=="normal" else "💥 انفجار ودخان!"
            draw_arabic_text(d,(x+142,y+15),label,font=_font(17,True),fill=glow,anchor="mm")
    draw_glow_panel(im,(45,470,1155,590),outline="#29324a",glow=RED if seconds_left<=3 else CYAN);d=ImageDraw.Draw(im)
    draw_arabic_text(d,(1100,495),"⏱ وقت المزاد",font=_font(19,True),fill=WHITE,anchor="ra")
    draw_progress_bar(d,(100,520,1080,553),seconds_left,maximum=10,track="#351c2b",fill=RED if seconds_left<=3 else CYAN,radius=12)
    draw_arabic_text(d,(90,505),"{:.1f}s".format(max(0,seconds_left)),font=_font(17,True),fill=RED if seconds_left<=3 else CYAN)
    leader=next((p for p in players if p.user_id==leader_id),None)
    draw_glow_panel(im,(45,610,1155,750),outline="#6b542b",glow="#f7c76a");d=ImageDraw.Draw(im)
    draw_arabic_text(d,(1100,640),"أعلى مزايدة",font=_font(19,True),fill="#f7c76a",anchor="ra")
    draw_arabic_text(d,(820,690),"$"+format(current_bid,","),font=_font(32,True),fill=WHITE,anchor="mm")
    if leader:
        av=(avatars or {}).get(leader.user_id)
        if av:draw_status_avatar(im,av,(90,650),60,status="alive");d=ImageDraw.Draw(im)
        draw_arabic_text(d,(175,685),leader.name[:24],font=_font(20,True),fill=WHITE)
    else:draw_arabic_text(d,(175,685),"لم تبدأ المزايدة",font=_font(19,True),fill=MUTED)
    if result:
        kind=result.get("content");label="كنز أسطوري ×{}".format(result.get("multiplier",3)) if kind=="legendary" else "خزنة آمنة +20%" if kind=="normal" else "فخ! خسرت المبلغ و3 نقاط"
        draw_arabic_text(d,(600,780),label,font=_font(17,True),fill=RED if kind=="trap" else GREEN,anchor="mm")
    return im

class VaultAuctionView(discord.ui.View):
    def __init__(self,g):super().__init__(timeout=None);self.g=g
    async def bid(self,i,n):
        try:await self.g.place_bid(int(i.user.id),n)
        except ValueError as e:await i.response.send_message(str(e),ephemeral=True);return
        await i.response.send_message("تمت المزايدة +$"+str(n)+". السعر الحالي $"+format(self.g.round.current_bid,","),ephemeral=True)
    @discord.ui.button(label="+100",style=discord.ButtonStyle.primary,row=0)
    async def b100(self,i,b):await self.bid(i,100)
    @discord.ui.button(label="+250",style=discord.ButtonStyle.primary,row=0)
    async def b250(self,i,b):await self.bid(i,250)
    @discord.ui.button(label="+500",style=discord.ButtonStyle.primary,row=0)
    async def b500(self,i,b):await self.bid(i,500)
    @discord.ui.button(label="انسحاب",style=discord.ButtonStyle.secondary,row=0)
    async def leave(self,i,b):
        try:await self.g.withdraw(int(i.user.id))
        except ValueError as e:await i.response.send_message(str(e),ephemeral=True);return
        await i.response.send_message("انسحبت من الجولة.",ephemeral=True)
    @discord.ui.button(label="المسبار السري",style=discord.ButtonStyle.success,row=1)
    async def probe(self,i,b):
        if int(i.user.id)!=self.g.round.probe_user_id:await i.response.send_message("المسبار مخصص للاعب آخر.",ephemeral=True);return
        if self.g.probe_claimed:await i.response.send_message("استُخدم المسبار.",ephemeral=True);return
        self.g.probe_claimed=True;await i.response.send_message("🛰️ تلميح مؤكد: "+self.g.round.probe_hint,ephemeral=True)

class BlindVaultsGame:
    def __init__(self,cog,members,rng=None):
        self.cog=cog;self.players=[VaultPlayer(m) for m in members];self.rng=rng or random.Random();self.round_number=1
        self.round=AuctionRound.create(1,self.players,self.rng);self.message=None;self.avatars={};self.probe_claimed=False;self.result=None
        self.event=asyncio.Event();self.lock=asyncio.Lock();self.view=VaultAuctionView(self);self.finished=False
    async def place_bid(self,uid,n):
        async with self.lock:
            p=next((p for p in self.players if p.user_id==uid),None)
            if not p:raise ValueError("أنت لست لاعبًا.")
            if self.finished or self.round.resolved:raise ValueError("انتهت الجولة.")
            if p.status!="active" or uid in self.round.withdrawn:raise ValueError("أنت منسحب أو خارج المزاد.")
            validate_bid(p,n,self.round.current_bid);bid=self.round.current_bid+n
            if bid>p.cash:raise ValueError("رصيدك لا يكفي.")
            self.round.current_bid=bid;self.round.leader_id=uid;self.event.set()
    async def withdraw(self,uid):
        async with self.lock:
            if self.finished or self.round.resolved:raise ValueError("انتهت الجولة.")
            p=next((p for p in self.players if p.user_id==uid),None)
            if not p or p.status!="active":raise ValueError("لا يمكنك الانسحاب.")
            self.round.withdrawn.add(uid)
            if self.round.leader_id==uid:self.round.leader_id=None;self.round.current_bid=0
            self.event.set()
    def file(self,t=10):return image_to_file(render_vault_board(self.players,round_number=self.round_number,current_bid=self.round.current_bid,leader_id=self.round.leader_id,seconds_left=t,result=self.result,avatars=self.avatars),"prime-blind-vaults.png")
    def embed(self):
        leader=next((p for p in self.players if p.user_id==self.round.leader_id),None)
        if self.round.resolved and self.result:
            names={"legendary":"كنز أسطوري","normal":"مكسب عادي","trap":"فخ مفخخ"}
            text="الخزنة {}: **{}**. الفائز: {}".format(self.result["vault"],names[self.result["content"]],next((p.name for p in self.players if p.user_id==self.result.get("winner_id")),"لا أحد"))
        else:text="الجولة {}/3 • أعلى مزايدة: {} • المتصدر: {}\n+100/+250/+500. المؤقت 10 ثوانٍ ويتجدد مع كل مزايدة.".format(self.round_number,"$"+format(self.round.current_bid,","),leader.name if leader else "لا أحد")
        return discord.Embed(title="🔐 Blind Vaults Auction",description=text,color=discord.Color.purple())
    async def refresh(self,t=10):
        if not self.message:return
        f=self.file(t)
        try:await self.message.edit(embed=self.embed(),attachments=[f],view=None if self.round.resolved or self.finished else self.view)
        except discord.HTTPException:logger.warning("Could not refresh vault board",exc_info=True)
        finally:f.close()
    async def start(self,msg):
        self.message=msg
        avs=await asyncio.gather(*(get_circular_avatar(p.member,56,session=getattr(self.cog.bot,"session",None),timeout=3) for p in self.players),return_exceptions=True)
        for p,a in zip(self.players,avs):
            if not isinstance(a,Exception):self.avatars[p.user_id]=a
        await self.refresh();asyncio.create_task(self.run_round())
    async def run_round(self):
        try:
            left=10.0
            while left>0 and not self.round.resolved:
                self.event=asyncio.Event();await self.refresh(left)
                try:await asyncio.wait_for(self.event.wait(),timeout=1)
                except asyncio.TimeoutError:left-=1
                else:left=10.0
            if self.round.leader_id is None:
                self.round.resolved=True;self.result={"winner_id":None,"vault":0,"content":"normal","payout":0,"multiplier":self.round.multiplier}
            else:self.result=resolve_auction_round(self.players,self.round,self.rng.choice(VAULTS))
            await self.refresh(0);await asyncio.sleep(2);self.round_number+=1
            if self.round_number>3 or not any(p.status=="active" for p in self.players):
                self.finished=True;await self.message.edit(embed=discord.Embed(title="انتهى المزاد",description=self.summary(),color=discord.Color.gold()),view=None);await self.cog.release(self);return
            self.round=AuctionRound.create(self.round_number,[p for p in self.players if p.status=="active"],self.rng)
            self.probe_claimed=False;self.result=None;await self.refresh();asyncio.create_task(self.run_round())
        except asyncio.CancelledError:raise
        except Exception:
            logger.exception("Blind Vaults crashed");self.finished=True;await self.cog.release(self)
    def summary(self):
        ps=sorted(self.players,key=lambda p:(p.points,p.cash),reverse=True)
        return "\n".join("{}. {} — {} • {} نقطة".format(i,p.name,"$"+format(p.cash,","),p.points) for i,p in enumerate(ps,1))

class BlindVaults(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.games={};self.lock=asyncio.Lock()
    async def release(self,g):
        gid=getattr(getattr(g.message,"guild",None),"id",None)
        if gid is not None:
            async with self.lock:
                if self.games.get(int(gid)) is g:self.games.pop(int(gid),None)
    @app_commands.command(name="blind_vaults",description="ابدأ مزاد الخزائن الغامضة")
    @app_commands.guild_only()
    async def blind_vaults(self,i:discord.Interaction):
        if i.guild is None or not isinstance(i.user,discord.Member):await i.response.send_message("اللعبة داخل السيرفر فقط.",ephemeral=True);return
        gid=int(i.guild.id)
        async with self.lock:
            if gid in self.games:await i.response.send_message("يوجد مزاد نشط.",ephemeral=True);return
        async def on_start(si,members):
            g=BlindVaultsGame(self,members)
            async with self.lock:
                if gid in self.games:raise RuntimeError("blind_vaults_already_active")
                self.games[gid]=g
            f=g.file()
            try:msg=await si.followup.send("🔐 بدأ مزاد الخزائن!",embed=g.embed(),file=f,view=g.view,wait=True)
            finally:f.close()
            await g.start(msg)
        lobby=UniversalLobbyView(i.user,game_name="Blind Vaults Auction",rules="3 جولات، 3 خزائن. الكنز الأسطوري 3×–4×، الخزنة العادية +20%، والفخ يخسرك المزايدة و3 نقاط. +100/+250/+500؛ المؤقت يتجدد لعشر ثوانٍ. لاعب واحد يحصل على تلميح مؤكد كل جولة.",on_start=on_start,min_players=2,max_players=8,timeout=300,avatar_session=getattr(self.bot,"session",None))
        f=await lobby.render_lobby_file()
        try:await i.response.send_message(embed=lobby.lobby_embed(),file=f,view=lobby)
        finally:f.close()
        await lobby.bind_message(await i.original_response())

async def setup(bot):await bot.add_cog(BlindVaults(bot))
