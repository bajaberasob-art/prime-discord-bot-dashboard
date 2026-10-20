"""The Heist: secret-choice visual multiplayer bank robbery."""
from __future__ import annotations
import asyncio, logging, math, random
from dataclasses import dataclass, field
from typing import Mapping, Sequence
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw
from game_engine import CYAN, GREEN, MUTED, PANEL_EDGE, PURPLE, RED, WHITE, _font, draw_arabic_text, draw_progress_bar, draw_status_avatar, get_circular_avatar, create_luxury_canvas, draw_glow_panel, image_to_file, UniversalLobbyView

logger=logging.getLogger("PrimeBankHeist")
MIN_PLAYERS,MAX_PLAYERS=3,8
STAGE_NAMES=("تجاوز الكاميرات والحراس","تفجير باب الخزنة","حزم سبائك الذهب","ركوب سيارة الهروب")
STAGE_HEAT=(14,20,24,28)
STAGE_LOOT=(1000,1800,2600,3600)
DECISIONS=("cooperate","withdraw","betray")

@dataclass
class HeistPlayer:
    member:object
    status:str="active"
    payout:int=0
    decision:str|None=None
    @property
    def user_id(self)->int:return int(getattr(self.member,"id"))
    @property
    def name(self)->str:return str(getattr(self.member,"display_name",getattr(self.member,"name","لاعب")))

@dataclass
class HeistState:
    players:list[HeistPlayer]
    stage:int=0
    loot:int=0
    heat:int=0
    phase:str="planning"
    finished:bool=False
    last_event:str="الفريق يستعد لاقتحام البنك."
    betrayal_winner_id:int|None=None
    history:list[dict[str,object]]=field(default_factory=list)
    def __post_init__(self):
        ids=[p.user_id for p in self.players]
        if len(ids)!=len(set(ids)):raise ValueError("duplicate_players")
        if not MIN_PLAYERS<=len(ids)<=MAX_PLAYERS:raise ValueError("invalid_player_count")
    @property
    def active_players(self):return [p for p in self.players if p.status=="active"]

def normalize_decision(decision:str,*,final_stage:bool)->str:
    if decision not in DECISIONS:raise ValueError("invalid_decision")
    if decision=="betray" and not final_stage:raise ValueError("betrayal_only_final_stage")
    return decision

def resolve_heist_stage(state:HeistState,decisions:Mapping[int,str],*,rng:random.Random|None=None,stage_success:bool|None=None,betrayal_success:bool|None=None)->dict[str,object]:
    """Resolve a stage; injected outcomes allow deterministic unit tests."""
    if state.finished or state.stage>=4:raise ValueError("game_already_finished")
    active=list(state.active_players)
    if not active:
        state.finished=True;state.phase="finished"
        return {"success":False,"arrested":[],"withdrawn":[],"loot":state.loot}
    rng=rng or random
    final=state.stage==3
    choices={p.user_id:normalize_decision(decisions.get(p.user_id,"cooperate"),final_stage=final) for p in active}
    coop=sum(v=="cooperate" for v in choices.values())
    heat=min(100,state.heat+STAGE_HEAT[state.stage]+max(0,coop-1)*5)
    chance=max(.15,min(.92,.86-state.heat/150+coop*.025))
    success=(rng.random()<chance) if stage_success is None else bool(stage_success)
    if heat>=100:success=False
    state.heat=heat
    if success:state.loot+=STAGE_LOOT[state.stage]*(1+coop)
    withdrawn=[]
    for p in active:
        p.decision=choices[p.user_id]
        if p.decision=="withdraw":p.status="withdrawn";withdrawn.append(p.user_id)
    # Withdrawals receive and remove their guaranteed equal share.
    withdrawal_share=state.loot//max(1,len(active))
    for p in active:
        if p.user_id in withdrawn:
            p.payout+=withdrawal_share;state.loot=max(0,state.loot-withdrawal_share)
    arrested=[]
    betrayers=[uid for uid,v in choices.items() if v=="betray"]
    if not success or state.heat>=100:
        for p in state.active_players:p.status="arrested";arrested.append(p.user_id)
        state.loot=0;state.finished=True;state.phase="finished"
        state.last_event="🚨 فشلت العملية! الشرطة قبضت على كل من بقي داخل البنك."
    elif final and betrayers:
        won=(rng.random()<.55) if betrayal_success is None else bool(betrayal_success)
        if len(betrayers)==1 and won:
            thief=next(p for p in active if p.user_id==betrayers[0])
            thief.status="betrayed";thief.payout+=state.loot;state.loot=0;state.betrayal_winner_id=thief.user_id
            for p in state.active_players:p.status="arrested";arrested.append(p.user_id)
            state.finished=True;state.phase="finished";state.last_event="{} خان الفريق واستولى على الغنيمة!".format(thief.name)
        else:
            share=state.loot//max(1,len(state.active_players))
            for p in state.active_players:p.payout+=share;p.status="winner"
            state.loot=0;state.finished=True;state.phase="finished";state.last_event="🤝 فشلت الخيانة؛ تقاسم الفريق الغنيمة المتبقية."
    else:
        state.stage+=1
        if state.stage>=4:
            share=state.loot//max(1,len(state.active_players))
            for p in state.active_players:p.payout+=share;p.status="winner"
            state.loot=0;state.finished=True;state.phase="finished";state.last_event="🚘 نجح الهروب! تقاسم الفريق الغنيمة."
        else:
            state.phase="planning";state.last_event="نجحت المرحلة؛ الغنيمة "+format(state.loot,",")+" والإنذار {}%.".format(state.heat)
    result={"stage":state.stage,"success":success,"withdrawn":withdrawn,"arrested":arrested,"heat":state.heat,"loot":state.loot,"finished":state.finished,"betrayal_winner_id":state.betrayal_winner_id}
    state.history.append(result.copy());return result

def render_heist_board(players:Sequence[HeistPlayer],*,stage:int,loot:int,heat:int,phase:str="planning",last_event:str="",avatars:Mapping[int,Image.Image]|None=None)->Image.Image:
    """Render vault art, a red heat meter, loot and each player's status."""
    ix=min(max(stage,0),3)
    image=create_luxury_canvas(1200,820,"THE HEIST • خيانة أو تقاسم","المرحلة {}/4 • {}".format(ix+1,STAGE_NAMES[ix]),accent=RED if heat>=65 else PURPLE)
    draw=ImageDraw.Draw(image);draw_glow_panel(image,(48,150,570,480),outline="#663344",glow=RED if heat>=65 else PURPLE);draw=ImageDraw.Draw(image)
    cx,cy=309,315
    for radius,fill,outline,width in [(145,"#202a3b","#8290a8",8),(112,"#111827","#4b5563",5),(72,"#263348","#9ca3af",5)]:
        draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill=fill,outline=outline,width=width)
    draw.rounded_rectangle((cx-18,cy-48,cx+18,cy+48),radius=10,fill="#f7c76a",outline="#fff1b8",width=3)
    for angle in range(0,360,45):
        x,y=int(cx+124*math.cos(math.radians(angle))),int(cy+124*math.sin(math.radians(angle)))
        draw.ellipse((x-7,y-7,x+7,y+7),fill="#d1d5db",outline="#475569")
    draw_arabic_text(draw,(309,470),"خزنة البنك المركزية",font=_font(18,True),fill=WHITE,anchor="mm")
    draw_glow_panel(image,(610,150,1150,310),outline=PANEL_EDGE,glow=RED if heat>=65 else CYAN);draw=ImageDraw.Draw(image)
    draw_arabic_text(draw,(880,184),"🚨 مستوى إنذار الشرطة",font=_font(23,True),fill=RED,anchor="mm")
    draw_progress_bar(draw,(660,215,1100,251),heat,maximum=100,track="#391a27",fill=RED,radius=14)
    draw_arabic_text(draw,(880,279),"{}% • {}".format(heat,"خطر حرج" if heat>=80 else "مراقبة متصاعدة" if heat>=50 else "الوضع تحت السيطرة"),font=_font(18,True),fill=RED if heat>=50 else CYAN,anchor="mm")
    draw_glow_panel(image,(610,335,1150,480),outline="#6b542b",glow="#f7c76a");draw=ImageDraw.Draw(image)
    draw_arabic_text(draw,(880,370),"💰 الغنيمة المشتركة",font=_font(23,True),fill="#f7c76a",anchor="mm")
    draw_arabic_text(draw,(880,424),"$"+format(loot,","),font=_font(38,True),fill=WHITE,anchor="mm")
    draw_arabic_text(draw,(880,458),"المرحلة {} من 4".format(ix+1),font=_font(15),fill=MUTED,anchor="mm")
    draw_glow_panel(image,(48,510,1150,770),outline=PANEL_EDGE);draw=ImageDraw.Draw(image);draw_arabic_text(draw,(1100,540),"الفريق وحالة كل لاعب",font=_font(21,True),fill=CYAN,anchor="ra")
    avatars=avatars or {}
    for i,p in enumerate(players):
        x,y=76+(i%4)*270,570+(i//4)*88
        labels={"active":("داخل البنك",GREEN),"withdrawn":("انسحب • $"+format(p.payout,","),CYAN),"arrested":("مقبوض عليه",RED),"winner":("ناجٍ • $"+format(p.payout,","),GREEN),"betrayed":("استولى • $"+format(p.payout,","),"#f7c76a")}
        label,color=labels.get(p.status,(p.status,MUTED));draw.rounded_rectangle((x,y,x+246,y+72),radius=14,fill="#111827",outline=color,width=2)
        avatar=avatars.get(p.user_id)
        if avatar is not None:
            draw_status_avatar(image,avatar,(x+10,y+13),46,status="eliminated" if p.status=="arrested" else "winner" if p.status in {"winner","betrayed"} else "alive");draw=ImageDraw.Draw(image)
        else:
            draw.ellipse((x+10,y+13,x+56,y+59),fill="#29233f",outline=color,width=2);draw_arabic_text(draw,(x+33,y+36),p.name[:1],font=_font(16,True),fill=WHITE,anchor="mm")
        draw_arabic_text(draw,(x+68,y+18),p.name[:17],font=_font(14,True),fill=WHITE);draw_arabic_text(draw,(x+68,y+43),label,font=_font(12,True),fill=color)
    draw_arabic_text(draw,(600,795),last_event[:120] or "كل قرار يغير مصير الفريق",font=_font(15,True),fill=MUTED,anchor="mm")
    return image

class HeistDecisionView(discord.ui.View):
    """Ephemeral choice buttons only visible to the player making the choice."""
    def __init__(self,game:"BankHeistGame",user_id:int):super().__init__(timeout=50);self.game=game;self.user_id=int(user_id)
    async def _choose(self,interaction:discord.Interaction,decision:str):
        if int(interaction.user.id)!=self.user_id:
            await interaction.response.send_message("هذه الأزرار ليست مخصصة لك.",ephemeral=True);return
        try:await self.game.submit_decision(self.user_id,decision)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        for item in self.children:item.disabled=True
        await interaction.response.edit_message(content="🔒 تم تسجيل قرارك بسرية.",view=self);self.stop()
    @discord.ui.button(label="🤝 تعاون تام",style=discord.ButtonStyle.success,row=0)
    async def cooperate(self,interaction:discord.Interaction,button:discord.ui.Button):await self._choose(interaction,"cooperate")
    @discord.ui.button(label="🏃 انسحاب آمن",style=discord.ButtonStyle.secondary,row=0)
    async def withdraw(self,interaction:discord.Interaction,button:discord.ui.Button):await self._choose(interaction,"withdraw")
    @discord.ui.button(label="🗡️ خيانة وسرقة كبرى",style=discord.ButtonStyle.danger,row=1)
    async def betray(self,interaction:discord.Interaction,button:discord.ui.Button):
        if self.game.state.stage!=3:await interaction.response.send_message("الخيانة متاحة في المرحلة الأخيرة فقط.",ephemeral=True);return
        await self._choose(interaction,"betray")

class HeistBoardView(discord.ui.View):
    def __init__(self,game:"BankHeistGame"):super().__init__(timeout=None);self.game=game
    @discord.ui.button(label="قراري السري",style=discord.ButtonStyle.primary,emoji="🔐")
    async def secret_decision(self,interaction:discord.Interaction,button:discord.ui.Button):
        p=next((p for p in self.game.state.active_players if p.user_id==int(interaction.user.id)),None)
        if p is None:await interaction.response.send_message("أنت لست لاعبًا نشطًا في هذه العملية.",ephemeral=True);return
        if p.decision is not None:await interaction.response.send_message("تم تسجيل قرارك بالفعل لهذه المرحلة.",ephemeral=True);return
        view=HeistDecisionView(self.game,p.user_id)
        if self.game.state.stage!=3:view.betray.disabled=True
        await interaction.response.send_message("المرحلة {}/4: اختر سرًا. الغنيمة الحالية $".format(self.game.state.stage+1)+format(self.game.state.loot,",")+" والإنذار {}%.".format(self.game.state.heat),view=view,ephemeral=True)

class BankHeistGame:
    def __init__(self,cog:"BankHeist",members:Sequence[object]):
        self.cog=cog;self.state=HeistState([HeistPlayer(m) for m in members]);self.message:discord.Message|None=None;self.avatars:dict[int,Image.Image]={}
        self._decision_event=asyncio.Event();self._decision_lock=asyncio.Lock();self._runner:asyncio.Task|None=None;self.closed=False;self.control_view=HeistBoardView(self)
    async def submit_decision(self,user_id:int,decision:str):
        async with self._decision_lock:
            if self.state.finished or self.state.phase!="planning":raise ValueError("انتهى وقت القرار لهذه المرحلة.")
            p=next((p for p in self.state.active_players if p.user_id==int(user_id)),None)
            if p is None:raise ValueError("لم تعد مشاركًا داخل البنك.")
            if p.decision is not None:raise ValueError("سجلت قرارك لهذه المرحلة بالفعل.")
            p.decision=normalize_decision(decision,final_stage=self.state.stage==3)
            if all(p.decision is not None for p in self.state.active_players):self._decision_event.set()
    def render_file(self):return image_to_file(render_heist_board(self.state.players,stage=self.state.stage,loot=self.state.loot,heat=self.state.heat,phase=self.state.phase,last_event=self.state.last_event,avatars=self.avatars),"prime-bank-heist.png")
    def embed(self):
        desc=("المرحلة: {}/4 — {}\nالغنيمة: $\nإنذار الشرطة: **{}%**\n\nاضغط «قراري السري» ثم اختر من الرسالة الخاصة بك.".format(min(self.state.stage+1,4),STAGE_NAMES[min(self.state.stage,3)],self.state.heat))
        desc=desc.replace("الغنيمة: $","الغنيمة: $"+format(self.state.loot,","))
        if self.state.finished:desc=self.state.last_event
        e=discord.Embed(title="🏦 The Heist • خيانة أو تقاسم",description=desc,color=discord.Color.from_rgb(255,85,119) if self.state.heat>=65 else discord.Color.from_rgb(168,85,247));e.set_footer(text="PRIME • THE HEIST • القرار السري يصنع النهاية");return e
    async def refresh(self):
        if self.message is None:return
        f=self.render_file()
        try:await self.message.edit(embed=self.embed(),attachments=[f],view=None if self.state.finished else self.control_view)
        except discord.HTTPException:logger.warning("Could not update bank heist board",exc_info=True)
        finally:f.close()
    async def start(self,message:discord.Message):
        self.message=message;session=getattr(self.cog.bot,"session",None)
        results=await asyncio.gather(*(get_circular_avatar(p.member,56,session=session,timeout=3.0) for p in self.state.players),return_exceptions=True)
        for p,r in zip(self.state.players,results):
            if not isinstance(r,Exception):self.avatars[p.user_id]=r
        await self.refresh();self._runner=asyncio.create_task(self.run(),name="prime-bank-heist-{}".format(getattr(message,"id","pending")))
    async def run(self):
        try:
            while not self.state.finished and self.state.stage<4:
                self.state.phase="planning"
                for p in self.state.active_players:p.decision=None
                self._decision_event=asyncio.Event();await self.refresh()
                try:await asyncio.wait_for(self._decision_event.wait(),timeout=35.0)
                except asyncio.TimeoutError:
                    for p in self.state.active_players:
                        if p.decision is None:p.decision="cooperate"
                resolve_heist_stage(self.state,{p.user_id:p.decision or "cooperate" for p in self.state.active_players});await self.refresh()
                if not self.state.finished:await asyncio.sleep(2.5)
            self.state.finished=True;self.state.phase="finished";self.closed=True;await self.refresh()
        except asyncio.CancelledError:raise
        except Exception:
            logger.exception("Bank heist game crashed");self.state.finished=True;self.state.phase="finished";self.closed=True;await self.refresh()
        finally:self.control_view.stop();self.closed=True;await self.cog._release_game(self)

class BankHeist(commands.Cog):
    def __init__(self,bot:commands.Bot):self.bot=bot;self.active_games:dict[int,BankHeistGame]={};self._game_lock=asyncio.Lock()
    async def _release_game(self,game:BankHeistGame):
        gid=getattr(getattr(game.message,"guild",None),"id",None)
        if gid is None:return
        async with self._game_lock:
            if self.active_games.get(int(gid)) is game:self.active_games.pop(int(gid),None)
    @app_commands.command(name="bank_heist",description="ابدأ عملية سرقة البنك: خيانة أو تقاسم")
    @app_commands.guild_only()
    async def bank_heist(self,interaction:discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user,discord.Member):
            await interaction.response.send_message("هذه اللعبة متاحة داخل السيرفر فقط.",ephemeral=True);return
        gid=int(interaction.guild.id)
        async with self._game_lock:
            if gid in self.active_games:await interaction.response.send_message("توجد عملية سرقة نشطة في هذا السيرفر.",ephemeral=True);return
        async def on_start(start_interaction:discord.Interaction,members:list[discord.Member]):
            game=BankHeistGame(self,members)
            async with self._game_lock:
                if gid in self.active_games:raise RuntimeError("bank_heist_already_active")
                self.active_games[gid]=game
            try:
                f=game.render_file()
                try:message=await start_interaction.followup.send("🏦 بدأت عملية السرقة! اتخذ قرارك السري في كل مرحلة.",embed=game.embed(),file=f,view=game.control_view,wait=True)
                finally:f.close()
                await game.start(message)
            except Exception:await self._release_game(game);raise
        lobby=UniversalLobbyView(interaction.user,game_name="The Heist • خيانة أو تقاسم",
            rules=("1) أربع مراحل: تجاوز الحراس، تفجير الخزنة، حزم الذهب، ثم الهروب.\n2) التعاون يزيد الغنيمة المحتملة لكنه يرفع الإنذار.\n3) الانسحاب يمنح حصة آمنة من الغنيمة الحالية.\n4) فشل مرحلة أو بلوغ الإنذار 100% يقبض على من بقي داخل البنك.\n5) الخيانة متاحة في المرحلة الأخيرة فقط."),
            on_start=on_start,min_players=MIN_PLAYERS,max_players=MAX_PLAYERS,timeout=300,avatar_session=getattr(self.bot,"session",None))
        f=await lobby.render_lobby_file()
        try:await interaction.response.send_message(embed=lobby.lobby_embed(),file=f,view=lobby)
        finally:f.close()
        await lobby.bind_message(await interaction.original_response())

async def setup(bot:commands.Bot):await bot.add_cog(BankHeist(bot))
