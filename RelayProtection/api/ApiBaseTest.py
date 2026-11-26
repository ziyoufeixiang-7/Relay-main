from api.BaseApi import BaseApi, ApiNodeData

class ApiBaseTest(BaseApi):
    MODULE_KEY = "raw_test"

    def _onSetup(self, params):
        nodes = {}
        for nodeId_str, nodeDict in params.get("nodes", {}).items():
            nodeId = int(nodeId_str)
            node = ApiNodeData(mode=nodeDict["mode"])
            node.base       = nodeDict.get("base")
            node.reset      = nodeDict.get("reset")
            node.steps      = nodeDict.get("steps")
            node.gate       = nodeDict.get("gate")
            node.interval   = nodeDict.get("interval")
            node.resetTime  = nodeDict.get("resetTime")
            node.resetDo    = nodeDict.get("resetDo")
            node.doActions  = nodeDict.get("doActions")
            node.countOverId  = nodeDict.get("countOverId")
            node.diMatchMask  = nodeDict.get("diMatchMask")
            node.diMatchId    = nodeDict.get("diMatchId")
            node.timeoutMs    = nodeDict.get("timeoutMs")
            node.timeoutId    = nodeDict.get("timeoutId")
            nodes[nodeId] = node
        self.ctrl.upsertNodes(nodes)

        self._startNode = params.get("startNode", None)
        if self._startNode is not None:
            self.ctrl.trigNode(self._startNode)

    def _onStop(self):
        pass
