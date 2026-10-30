"use client";
import {use} from "react";
import {Shell} from "../../../components/shell";
import {BacktestReport} from "../../../components/backtest-report";
export default function Page({params}:{params:Promise<{id:string}>}){const {id}=use(params);return <Shell>{me=><BacktestReport key={`${me.user.id}:${me.data_mode}:${id}`} me={me} id={id}/>}</Shell>;}
