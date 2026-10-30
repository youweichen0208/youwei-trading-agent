"use client";
import {use} from "react";
import {Shell} from "../../../components/shell";
import {TrainingEvidence} from "../../../components/training-evidence";
export default function Page({params}:{params:Promise<{id:string}>}){const {id}=use(params);return <Shell>{me=><TrainingEvidence key={`${me.user.id}:${me.data_mode}:${id}`} me={me} id={id}/>}</Shell>;}
