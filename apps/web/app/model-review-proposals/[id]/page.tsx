"use client";
import {use} from "react";
import {Shell} from "../../../components/shell";
import {ModelReview} from "../../../components/model-review";
export default function Page({params}:{params:Promise<{id:string}>}){const {id}=use(params);return <Shell>{me=><ModelReview key={`${me.user.id}:${me.data_mode}:${id}`} me={me} proposalId={id}/>}</Shell>;}
