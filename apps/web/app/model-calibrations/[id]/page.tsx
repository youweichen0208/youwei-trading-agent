"use client";
import {use} from "react";
import {Shell} from "../../../components/shell";
import {ModelCalibration} from "../../../components/model-calibration";
export default function Page({params}:{params:Promise<{id:string}>}){const {id}=use(params);return <Shell>{me=><ModelCalibration key={`${me.user.id}:${me.data_mode}:${id}`} me={me} id={id}/>}</Shell>;}
